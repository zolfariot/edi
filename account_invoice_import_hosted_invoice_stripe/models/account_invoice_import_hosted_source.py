# Copyright 2026 Akretion France (http://www.akretion.com/)
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import logging
import re
from urllib.parse import unquote, urlparse

import requests

from odoo import _, api, fields, models
from odoo.exceptions import UserError

logger = logging.getLogger(__name__)

STRIPE_HOSTED_API_URL = "https://invoicedata.stripe.com/hosted_invoice_page"
STRIPE_RECEIPT_API_URL = "https://invoicedata.stripe.com/invoice_receipt_file_url"
STRIPE_API_URL = "https://api.stripe.com/v1/invoices"
STRIPE_API_VERSION = "2026-06-24.dahlia"
STRIPE_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)
TIMEOUT = 30
# Stripe marketing e-mails wrap the real link in a click-tracking redirect
# such as https://59.email.stripe.com/CL0/<url-encoded-target>/1/<id>/<sig>
EMAIL_WRAPPER_RE = re.compile(
    r"^https?://[^/]+\.email\.stripe\.com/CL0/(.+?)/\d+/", re.IGNORECASE
)

# Stripe customer billing portal (list every invoice of a portal session).
STRIPE_PORTAL_SESSIONS_URL = "https://billing.stripe.com/v1/billing_portal/sessions"
STRIPE_PORTAL_PAGE_SIZE = 100
# API version used by the customer portal front-end for its invoice queries.
STRIPE_PORTAL_API_VERSION = "2025-06-30.basil"
# The portal page embeds bootstrap JSON in its HTML; the two values we need may
# appear with plain, backslash-escaped or HTML-entity-encoded quotes.
PORTAL_SESSION_ID_RE = re.compile(
    r"""portal_session_id(?:\\?["']|&quot;|&#34;)\s*:\s*"""
    r"""(?:\\?["']|&quot;|&#34;)([A-Za-z0-9_]+)"""
)
SESSION_API_KEY_RE = re.compile(
    r"""session_api_key(?:\\?["']|&quot;|&#34;)\s*:\s*"""
    r"""(?:\\?["']|&quot;|&#34;)([A-Za-z0-9_]+)"""
)


class StripePortalExpired(Exception):
    """Raised when a Stripe billing portal session URL is no longer valid.

    The caller is expected to obtain a fresh portal URL (e.g. by re-querying
    the supplier API with its own API key) and retry the listing.
    """


class AccountInvoiceImportHostedSource(models.Model):
    _inherit = "account.invoice.import.hosted.source"

    provider = fields.Selection(selection_add=[("stripe", "Stripe")])

    def _try_unwrap_url(self, url):
        match = EMAIL_WRAPPER_RE.match(url or "")
        if match:
            return unquote(match.group(1))
        return super()._try_unwrap_url(url)

    def _get_provider_from_url(self, normalized_url):
        provider = super()._get_provider_from_url(normalized_url)
        if provider:
            return provider
        parsed = urlparse(normalized_url)
        host = (parsed.hostname or "").lower()
        parts = [p for p in parsed.path.split("/") if p]
        if (
            parsed.scheme == "https"
            and host in ("invoice.stripe.com", "pay.stripe.com")
            and len(parts) >= 3
            and parts[0] in ("i", "invoice")
        ):
            return "stripe"
        return False

    def _fetch_provider_payload(self, provider, normalized_url):
        if provider != "stripe":
            return super()._fetch_provider_payload(provider, normalized_url)
        return self._stripe_fetch_payload(normalized_url)

    def _stripe_headers(self):
        return {
            "User-Agent": STRIPE_USER_AGENT,
            "Accept": "application/json",
        }

    def _stripe_extract_acct_secret(self, normalized_url):
        parsed = urlparse(normalized_url)
        parts = [p for p in parsed.path.split("/") if p]
        # path shape is either /i/{acct}/{secret} or /invoice/{acct}/{secret}[/pdf]
        return parts[1], parts[2]

    def _stripe_fetch_payload(self, normalized_url):
        acct, secret = self._stripe_extract_acct_secret(normalized_url)
        headers = self._stripe_headers()

        step1_url = f"{STRIPE_HOSTED_API_URL}/{acct}/{secret}"
        step1_resp = requests.get(step1_url, headers=headers, timeout=TIMEOUT)
        step1_resp.raise_for_status()
        step1_data = step1_resp.json()
        if step1_data.get("expired"):
            return {"expired": True}

        ephemeral_key = step1_data.get("ephemeral_key")
        invoice_id = step1_data.get("invoice_id")
        if not ephemeral_key or not invoice_id:
            raise UserError(
                _("Stripe did not return the expected invoice access data.")
            )
        merchant = step1_data.get("merchant") or {}

        step2_headers = dict(headers)
        step2_headers.update(
            {
                # Never log this header: it grants read access to the invoice.
                "Authorization": f"Bearer {ephemeral_key}",
                "Stripe-Version": STRIPE_API_VERSION,
            }
        )
        step2_url = f"{STRIPE_API_URL}/{invoice_id}/hosted"
        step2_resp = requests.get(step2_url, headers=step2_headers, timeout=TIMEOUT)
        step2_resp.raise_for_status()
        invoice_data = step2_resp.json()

        return {
            "supplier_candidates": [
                merchant.get("business_name"),
                merchant.get("support_email"),
            ],
            "invoice_number": invoice_data.get("number"),
            "invoice_date": self._stripe_parse_invoice_date(invoice_data),
            "due_date": self._stripe_parse_due_date(invoice_data),
            "currency_iso": (invoice_data.get("currency") or "").upper(),
            "lines": self._stripe_build_lines(invoice_data),
            "attachments": self._stripe_download_attachments(
                invoice_data, acct, secret, headers
            ),
        }

    def _stripe_parse_invoice_date(self, invoice_data):
        status_transitions = invoice_data.get("status_transitions") or {}
        return self._parse_date_epoch(status_transitions.get("finalized_at"))

    def _stripe_parse_due_date(self, invoice_data):
        due_date = invoice_data.get("due_date")
        if due_date:
            return self._parse_date_epoch(due_date)
        status_transitions = invoice_data.get("status_transitions") or {}
        return self._parse_date_epoch(status_transitions.get("paid_at"))

    def _stripe_build_lines(self, invoice_data):
        lines = []
        for line in (invoice_data.get("lines") or {}).get("data") or []:
            description = (
                line.get("hosted_invoice_product_name")
                or line.get("hosted_invoice_short_description")
                or line.get("description")
            )
            amount_cents = line.get("amount_excluding_tax", line.get("amount", 0))
            try:
                amount = float(amount_cents or 0) / 100.0
            except (TypeError, ValueError):
                amount = 0.0
            tax_amount = 0.0
            for tax in line.get("tax_amounts") or []:
                try:
                    tax_amount += float(tax.get("amount") or 0) / 100.0
                except (TypeError, ValueError):
                    continue
            period = line.get("period") or {}
            lines.append(
                {
                    "description": description,
                    "quantity": line.get("quantity") or 0.0,
                    "amount": amount,
                    "tax_amount": tax_amount,
                    "period_start": self._parse_date_epoch(period.get("start")),
                    "period_end": self._parse_date_epoch(period.get("end")),
                }
            )
        return lines

    def _stripe_download_attachments(self, invoice_data, acct, secret, headers):
        attachments = []
        pdf_url = invoice_data.get("invoice_pdf")
        if pdf_url:
            try:
                content = self._download_binary(pdf_url)
            except Exception:  # noqa: BLE001
                logger.warning(
                    "Could not download Stripe invoice PDF, skipping.",
                    exc_info=True,
                )
            else:
                attachments.append({"kind": "invoice", "content": content})

        try:
            receipt_url_resp = requests.get(
                f"{STRIPE_RECEIPT_API_URL}/{acct}/{secret}",
                headers=headers,
                timeout=TIMEOUT,
            )
            receipt_url_resp.raise_for_status()
            file_url = receipt_url_resp.json().get("file_url")
            if file_url:
                content = self._download_binary(file_url)
                attachments.append({"kind": "receipt", "content": content})
        except Exception:  # noqa: BLE001
            logger.warning(
                "Could not download Stripe receipt PDF, skipping.", exc_info=True
            )

        return attachments

    # ------------------------------------------------------------------
    # Reusable Stripe billing portal invoice lister
    #
    # Given a (short-lived) Stripe customer billing portal session URL, list
    # every invoice of that session and return their public hosted invoice
    # URLs. Each URL can then be fed back into the standard hosted invoice
    # pipeline (``_fetch_provider_payload('stripe', url)``) to build a full
    # vendor bill with lines, product mapping and PDF attachments.
    #
    # Any supplier that exposes a Stripe billing portal URL (directly or via
    # its own API) can reuse this. Portal URLs expire, so callers should be
    # ready to catch ``StripePortalExpired``, fetch a fresh URL and retry.
    # ------------------------------------------------------------------
    @api.model
    def _stripe_portal_extract_session(self, portal_html):
        """Extract (portal_session_id, session_api_key) from the portal HTML.

        Returns ``(False, False)`` when the bootstrap values cannot be found
        (typically because the session has expired).
        """
        html = portal_html or ""
        session_match = PORTAL_SESSION_ID_RE.search(html)
        key_match = SESSION_API_KEY_RE.search(html)
        portal_session_id = session_match.group(1) if session_match else False
        session_api_key = key_match.group(1) if key_match else False
        return portal_session_id, session_api_key

    @api.model
    def _stripe_portal_open_session(self, portal_url):
        """Fetch the portal page and return (portal_session_id, session_api_key).

        Raises ``StripePortalExpired`` when the session is no longer valid.
        """
        headers = {"User-Agent": STRIPE_USER_AGENT}
        try:
            response = requests.get(
                portal_url,
                headers=headers,
                timeout=TIMEOUT,
                allow_redirects=True,
            )
        except requests.RequestException as exc:
            raise UserError(
                _("Could not reach the Stripe billing portal: %s") % exc
            ) from exc
        if response.status_code in (401, 403, 404, 410):
            raise StripePortalExpired(portal_url)
        response.raise_for_status()
        portal_session_id, session_api_key = self._stripe_portal_extract_session(
            response.text
        )
        if not portal_session_id or not session_api_key:
            raise StripePortalExpired(portal_url)
        return portal_session_id, session_api_key

    @api.model
    def _stripe_portal_list_invoice_urls(self, portal_url, since_date=None):
        """List the hosted invoice URLs of a Stripe billing portal session.

        ``portal_url``: a fresh Stripe customer billing portal session URL.
        ``since_date``: optional ISO ``YYYY-MM-DD`` lower bound; invoices whose
        effective/finalized date is strictly before it are skipped.

        Returns a list of dicts ``{'hosted_invoice_url', 'date', 'status',
        'stripe_id'}`` in Stripe order (most recent first). Raises
        ``StripePortalExpired`` when the session has expired.
        """
        portal_session_id, session_api_key = self._stripe_portal_open_session(
            portal_url
        )
        # Accept both a date object and an ISO string for the lower bound.
        if since_date and not isinstance(since_date, str):
            since_date = fields.Date.to_string(since_date)
        headers = {
            "User-Agent": STRIPE_USER_AGENT,
            "Accept": "application/json",
            "X-Requested-With": "XMLHttpRequest",
            "Stripe-Version": STRIPE_PORTAL_API_VERSION,
            # Never log this header: it grants read access to the invoices.
            "Authorization": f"Bearer {session_api_key}",
        }
        list_url = f"{STRIPE_PORTAL_SESSIONS_URL}/{portal_session_id}/invoices"
        # Stripe expects a *single* ``include_only[]`` value holding a
        # comma-separated field list (repeating the param yields HTTP 400).
        include_only = ",".join(
            [
                "object",
                "has_more",
                "data.id",
                "data.object",
                "data.status",
                "data.hosted_invoice_url",
                "data.effective_at",
                "data.finalized_at",
                "data.created",
            ]
        )
        base_params = [
            ("limit", STRIPE_PORTAL_PAGE_SIZE),
            ("include_only[]", include_only),
        ]
        results = []
        starting_after = None
        for _page in range(100):  # hard safety bound on pagination
            params = list(base_params)
            if starting_after:
                params.append(("starting_after", starting_after))
            try:
                response = requests.get(
                    list_url, headers=headers, params=params, timeout=TIMEOUT
                )
            except requests.RequestException as exc:
                raise UserError(
                    _("Could not list Stripe billing portal invoices: %s") % exc
                ) from exc
            if response.status_code in (401, 403):
                raise StripePortalExpired(portal_url)
            response.raise_for_status()
            body = response.json()
            page = body.get("data") or []
            if not page:
                break
            page_below_since = False
            for inv in page:
                hosted_url = inv.get("hosted_invoice_url")
                inv_date = self._parse_date_epoch(
                    inv.get("effective_at")
                    or inv.get("finalized_at")
                    or inv.get("created")
                )
                if since_date and inv_date and inv_date < since_date:
                    page_below_since = True
                    continue
                if not hosted_url:
                    continue
                results.append(
                    {
                        "hosted_invoice_url": hosted_url,
                        "date": inv_date,
                        "status": inv.get("status"),
                        "stripe_id": inv.get("id"),
                    }
                )
            if not body.get("has_more"):
                break
            # Stripe returns invoices most-recent first; once a whole page is
            # older than the lower bound there is nothing newer to gain.
            if since_date and page_below_since:
                break
            starting_after = page[-1].get("id")
            if not starting_after:
                break
        return results
