# Copyright 2026 Akretion France (http://www.akretion.com/)
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import json
import logging
import re
from datetime import date, datetime, timezone
from urllib.parse import urljoin, urlparse

import requests
from dateutil import parser as date_parser
from lxml import html

from odoo import _, fields, models
from odoo.exceptions import UserError

logger = logging.getLogger(__name__)

_STRIPE_API_VERSION = "2026-06-24.dahlia"
_STRIPE_USER_AGENT = "Mozilla/5.0"


class AccountInvoiceImportHostedSource(models.Model):
    _inherit = "account.invoice.import.hosted.source"

    def _get_provider_from_url(self, normalized_url):
        provider = super()._get_provider_from_url(normalized_url)
        if provider:
            return provider
        parsed = urlparse(normalized_url)
        host = (parsed.hostname or "").lower()
        if (
            parsed.scheme == "https"
            and host == "invoice.stripe.com"
            and parsed.path.startswith("/i/")
        ):
            return "stripe"
        return False

    def _fetch_provider_payload(self, provider, normalized_url):
        if provider != "stripe":
            return super()._fetch_provider_payload(provider, normalized_url)
        return self._stripe_fetch_provider_payload(normalized_url)

    def _stripe_fetch_provider_payload(self, normalized_url):
        page_response = self._stripe_http_get(normalized_url)
        document = html.fromstring(page_response.text or "")
        document.make_links_absolute(page_response.url)
        metadata = self._stripe_extract_metadata(document, page_response.text or "")
        links = self._stripe_extract_pdf_links(
            document, page_response.text or "", page_response.url
        )
        attachments = []
        for kind, link in links.items():
            if not link:
                continue
            content = self._stripe_download_binary(link)
            if not content:
                continue
            filename = self._stripe_attachment_filename(kind, link)
            attachments.append({"filename": filename, "content": content})
        metadata["attachments"] = attachments

        # Fallback to the Stripe hosted API when HTML extraction yields nothing
        # useful (JS-only shell page).
        if self._stripe_should_use_hosted_api(metadata):
            logger.info(
                "Stripe HTML extraction yielded no metadata/attachments; "
                "falling back to hosted API flow."
            )
            return self._stripe_hosted_api_fetch(normalized_url)

        return metadata

    def _stripe_should_use_hosted_api(self, metadata):
        """Return True when HTML extraction produced no useful data."""
        has_attachments = bool(metadata.get("attachments"))
        has_invoice_number = bool(metadata.get("invoice_number"))
        has_amount = metadata.get("amount_total") is not None
        return not has_attachments and not has_invoice_number and not has_amount

    def _stripe_hosted_api_fetch(self, normalized_url):
        """2-step Stripe hosted invoice API flow for JS-shell pages.

        Step 1 – GET invoicedata.stripe.com/hosted_invoice_page/{acct}/{secret}
                 Returns JSON with ``ephemeral_key`` and ``invoice_id``.
        Step 2 – GET api.stripe.com/v1/invoices/{invoice_id}/hosted
                 Requires ``Authorization: ******`` and ``Stripe-Version`` headers.
        """
        parsed = urlparse(normalized_url)
        # Path is /i/{acct}/{secret}[/{extra}]
        path_parts = parsed.path.strip("/").split("/")
        if len(path_parts) < 3 or path_parts[0] != "i":
            raise UserError(
                _("Cannot parse account/secret from Stripe URL '%(url)s'.", url=parsed.path)
            )
        acct = path_parts[1]
        secret = path_parts[2]

        browser_headers = {
            "User-Agent": _STRIPE_USER_AGENT,
            "Accept": "application/json, text/plain, */*",
            "Origin": "https://invoice.stripe.com",
            "Referer": normalized_url,
        }

        # Step 1 – fetch ephemeral_key and invoice_id
        step1_url = (
            f"https://invoicedata.stripe.com/hosted_invoice_page/{acct}/{secret}"
            "?creditNoteRecoverySlug="
        )
        logger.info("Stripe hosted API step1: fetching invoice page data.")
        step1_resp = self._stripe_hosted_api_get(step1_url, headers=browser_headers)
        step1_data = step1_resp.json()
        ephemeral_key = step1_data.get("ephemeral_key")
        invoice_id = step1_data.get("invoice_id")
        if not ephemeral_key or not invoice_id:
            raise UserError(
                _(
                    "Stripe hosted API step1 did not return ephemeral_key/invoice_id "
                    "(invoice_id=%(inv)s).",
                    inv=invoice_id,
                )
            )

        # Step 2 – fetch invoice metadata using the ephemeral key
        step2_url = f"https://api.stripe.com/v1/invoices/{invoice_id}/hosted"
        step2_headers = dict(browser_headers)
        step2_headers.update(
            {
                "Authorization": f"Bearer {ephemeral_key}",
                "Stripe-Version": _STRIPE_API_VERSION,
                "Accept": "application/json",
            }
        )
        logger.info("Stripe hosted API step2: fetching invoice metadata.")
        step2_resp = self._stripe_hosted_api_get(step2_url, headers=step2_headers)
        invoice_data = step2_resp.json()

        return self._stripe_map_hosted_api_payload(invoice_data, normalized_url)

    def _stripe_hosted_api_get(self, url, headers=None):
        """HTTP GET helper used exclusively for the hosted API flow."""
        response = requests.get(url, headers=headers, timeout=30, allow_redirects=True)
        response.raise_for_status()
        return response

    def _stripe_map_hosted_api_payload(self, invoice_data, normalized_url):
        """Map a Stripe /v1/invoices/{id}/hosted response to the standard payload."""
        currency = (invoice_data.get("currency") or "").upper()
        total = invoice_data.get("total")
        subtotal = invoice_data.get("subtotal")
        status_ts = (invoice_data.get("status_transitions") or {}).get("finalized_at")

        metadata = {
            "invoice_number": invoice_data.get("number") or False,
            "currency_iso": currency or False,
            "amount_total": total / 100 if total is not None else None,
            "amount_untaxed": subtotal / 100 if subtotal is not None else None,
            "invoice_date": self._stripe_parse_date(status_ts),
            "due_date": self._stripe_parse_date(invoice_data.get("due_date")),
            "description": "Stripe hosted invoice",
            "attachments": [],
        }

        pdf_url = invoice_data.get("invoice_pdf")
        if pdf_url:
            content = self._stripe_download_binary(pdf_url)
            if content:
                inv_num = metadata.get("invoice_number")
                filename = (
                    f"stripe_invoice_{inv_num}.pdf" if inv_num else "stripe_invoice.pdf"
                )
                metadata["attachments"].append(
                    {"filename": filename, "content": content}
                )

        return metadata

    def _stripe_http_get(self, url):
        response = requests.get(url, timeout=30, allow_redirects=True)
        response.raise_for_status()
        return response

    def _stripe_download_binary(self, url):
        response = requests.get(url, timeout=30, allow_redirects=True)
        response.raise_for_status()
        return response.content

    def _stripe_extract_pdf_links(self, document, raw_html, base_url):
        invoice_url = False
        receipt_url = False
        for node in document.xpath("//a[@href]"):
            href = (node.get("href") or "").strip()
            if not href:
                continue
            absolute_url = urljoin(base_url, href)
            merged = f"{(node.text_content() or '').lower()} {href.lower()}"
            if "receipt" in merged and "pdf" in merged and not receipt_url:
                receipt_url = absolute_url
            elif "invoice" in merged and "pdf" in merged and not invoice_url:
                invoice_url = absolute_url
            elif href.lower().endswith(".pdf"):
                if not invoice_url:
                    invoice_url = absolute_url
                elif not receipt_url:
                    receipt_url = absolute_url

        if not invoice_url or not receipt_url:
            for regex in (
                r"https?://[^\s\"']+\.pdf[^\s\"']*",
                r"/[^\s\"']+\.pdf[^\s\"']*",
            ):
                for match in re.findall(regex, raw_html or ""):
                    absolute_url = urljoin(base_url, match.replace("\\/", "/"))
                    low = absolute_url.lower()
                    if "receipt" in low and not receipt_url:
                        receipt_url = absolute_url
                    elif "invoice" in low and not invoice_url:
                        invoice_url = absolute_url
                    elif not invoice_url:
                        invoice_url = absolute_url
                    elif not receipt_url:
                        receipt_url = absolute_url

        return {"invoice": invoice_url, "receipt": receipt_url}

    def _stripe_extract_metadata(self, document, raw_html):
        data = {
            "invoice_number": False,
            "invoice_date": False,
            "due_date": False,
            "currency_iso": False,
            "amount_untaxed": None,
            "amount_total": None,
            "description": "Stripe hosted invoice",
        }

        plain_text = " ".join(document.xpath("//text()"))
        plain_text = re.sub(r"\s+", " ", plain_text or "").strip()
        self._stripe_extract_metadata_from_jsonld(document, data)
        self._stripe_extract_text_metadata(plain_text, raw_html, data)
        return data

    def _stripe_extract_metadata_from_jsonld(self, document, data):
        for script_text in document.xpath(
            "//script[@type='application/ld+json']/text()"
        ):
            try:
                script_data = json.loads(script_text)
            except Exception:
                continue
            if isinstance(script_data, dict):
                invoice_number = script_data.get("identifier") or script_data.get(
                    "invoiceNumber"
                )
                if invoice_number and not data["invoice_number"]:
                    data["invoice_number"] = str(invoice_number)
                date_value = script_data.get("dateIssued") or script_data.get(
                    "dateCreated"
                )
                if date_value and not data.get("invoice_date"):
                    data["invoice_date"] = self._stripe_parse_date(date_value)

    def _stripe_extract_text_metadata(self, plain_text, raw_html, data):
        if not data.get("invoice_number"):
            match = re.search(
                r"(?:Invoice(?:\s+number)?|Invoice\s*#)\s*[:#]?\s*([A-Za-z0-9\-_/]+)",
                plain_text,
                re.IGNORECASE,
            )
            if match:
                data["invoice_number"] = match.group(1)
        self._stripe_extract_dates(plain_text, data)
        self._stripe_extract_amounts(plain_text, data)
        if not data.get("currency_iso"):
            json_currency = re.search(
                r'"currency"\s*:\s*"([a-zA-Z]{3})"', raw_html or ""
            )
            if json_currency:
                data["currency_iso"] = json_currency.group(1).upper()
        if data.get("currency_iso"):
            for key in ("amount_total", "amount_untaxed"):
                if data.get(key) is not None:
                    data[key] = round(data[key], 2)
        if data.get("amount_untaxed") is None and data.get("amount_total") is not None:
            data["amount_untaxed"] = data["amount_total"]

    def _stripe_extract_dates(self, plain_text, data):
        for key, label in (("invoice_date", "Invoice date"), ("due_date", "Due date")):
            if data.get(key):
                continue
            match = re.search(
                rf"{label}\s*:?\s*([A-Za-z]{{3,9}}\s+\d{{1,2}},\s+\d{{4}}|\d{{4}}-\d{{2}}-\d{{2}})",
                plain_text,
                re.IGNORECASE,
            )
            if match:
                data[key] = self._stripe_parse_date(match.group(1))

    def _stripe_extract_amounts(self, plain_text, data):
        money_patterns = (
            (
                "amount_untaxed",
                r"(?:Subtotal|Untaxed)\s*:?\s*([A-Z]{3}|[$€£])?\s*"
                r"([0-9][0-9,]*\.?[0-9]{0,2})",
            ),
            (
                "amount_total",
                r"(?:Total|Amount due)\s*:?\s*([A-Z]{3}|[$€£])?\s*"
                r"([0-9][0-9,]*\.?[0-9]{0,2})",
            ),
        )
        for key, pattern in money_patterns:
            match = re.search(pattern, plain_text, re.IGNORECASE)
            if not match:
                continue
            currency_hint = match.group(1)
            amount = self._stripe_parse_amount(match.group(2))
            if amount is not None:
                data[key] = amount
            if currency_hint and not data.get("currency_iso"):
                data["currency_iso"] = self._stripe_currency_from_hint(currency_hint)

    def _stripe_currency_from_hint(self, hint):
        hint = (hint or "").upper()
        return {
            "$": "USD",
            "€": "EUR",
            "£": "GBP",
        }.get(hint, hint if len(hint) == 3 else False)

    def _stripe_parse_amount(self, amount_text):
        if amount_text is None:
            return None
        cleaned = str(amount_text).replace(",", "").strip()
        if not cleaned:
            return None
        try:
            return float(cleaned)
        except Exception:
            return None

    def _stripe_parse_date(self, date_text):
        if not date_text:
            return False
        # Stripe API returns Unix timestamps as integers for date fields
        if isinstance(date_text, (int, float)):
            try:
                parsed = datetime.fromtimestamp(date_text, tz=timezone.utc).date()
                return fields.Date.to_string(parsed)
            except Exception:
                return False
        try:
            parsed = date_parser.parse(str(date_text)).date()
        except Exception:
            return False
        if isinstance(parsed, date):
            return fields.Date.to_string(parsed)
        return False

    def _stripe_attachment_filename(self, kind, url):
        parsed = urlparse(url)
        filename = (parsed.path.rsplit("/", 1)[-1] or "").split("?", 1)[0]
        if filename and filename.lower().endswith(".pdf"):
            return filename
        return f"stripe_{kind}.pdf"
