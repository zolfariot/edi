# Copyright 2026 Akretion France (http://www.akretion.com/)
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
import hashlib
import json
import logging
import re
import unicodedata
from datetime import date, datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests
from dateutil import parser as date_parser

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import float_is_zero

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 30


class AccountInvoiceImportHostedSource(models.Model):
    _name = "account.invoice.import.hosted.source"
    _description = "Hosted Invoice Link Import Source"
    _order = "id desc"

    company_id = fields.Many2one(
        "res.company", required=True, default=lambda self: self.env.company
    )
    source_url = fields.Char(
        string="Invoice Link", required=True, index=True
    )
    normalized_url = fields.Char(readonly=True, copy=False)
    source_url_hash = fields.Char(readonly=True, copy=False, index=True)
    provider = fields.Selection([], readonly=True, copy=False)
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("mapping_required", "Mapping Required"),
            ("expired", "Expired"),
            ("duplicate", "Duplicate"),
            ("processed", "Processed"),
            ("error", "Error"),
        ],
        default="draft",
        readonly=True,
        copy=False,
    )
    partner_id = fields.Many2one("res.partner", readonly=True, copy=False)
    forced_partner_id = fields.Many2one(
        "res.partner",
        readonly=True,
        copy=False,
        help="When set, the supplier is already known (e.g. an API-driven feed "
        "that lists the invoices of a given vendor), so supplier mapping "
        "resolution is skipped and this partner is used directly.",
    )
    supplier_external_key = fields.Char(readonly=True, copy=False)
    missing_mapping_info = fields.Text(readonly=True, copy=False)
    invoice_number = fields.Char(readonly=True, copy=False)
    invoice_date = fields.Date(readonly=True, copy=False)
    invoice_due_date = fields.Date(readonly=True, copy=False)
    currency_id = fields.Many2one("res.currency", readonly=True, copy=False)
    amount_untaxed = fields.Monetary(
        readonly=True, copy=False, currency_field="currency_id"
    )
    amount_total = fields.Monetary(
        readonly=True, copy=False, currency_field="currency_id"
    )
    move_id = fields.Many2one(
        "account.move", readonly=True, copy=False, ondelete="set null"
    )
    error_message = fields.Text(readonly=True, copy=False)
    process_date = fields.Datetime(readonly=True, copy=False)
    raw_payload = fields.Text(readonly=True, copy=False)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("source_url"):
                normalized = self._normalize_url(vals["source_url"])
                vals["normalized_url"] = normalized
                vals["source_url_hash"] = self._hash_url(normalized)
        return super().create(vals_list)

    # ------------------------------------------------------------------
    # Generic URL helpers
    # ------------------------------------------------------------------
    @api.model
    def _normalize_url(self, raw_url):
        raw_url = (raw_url or "").strip()
        if not raw_url:
            return raw_url
        parsed = urlsplit(raw_url)
        query = urlencode(sorted(parse_qsl(parsed.query, keep_blank_values=True)))
        return urlunsplit(
            (parsed.scheme.lower(), parsed.netloc.lower(), parsed.path, query, "")
        )

    @api.model
    def _hash_url(self, normalized_url):
        return hashlib.sha256((normalized_url or "").encode("utf-8")).hexdigest()

    @api.model
    def _normalize_key(self, text):
        if not text:
            return ""
        text = unicodedata.normalize("NFKD", str(text))
        text = text.encode("ascii", "ignore").decode("ascii")
        text = text.lower().strip()
        text = re.sub(r"\s+", " ", text)
        return text

    @api.model
    def _slugify(self, text):
        text = self._normalize_key(text)
        text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
        return text or "supplier"

    @api.model
    def _supplier_filename_abbrev(self, partner):
        """Return the supplier abbreviation used in attachment filenames.

        Uses ``partner.ref`` when defined, otherwise falls back to the
        partner name. In both cases whitespace is collapsed to underscores.
        """
        source = (partner.ref or "").strip() or (partner.name or "supplier")
        abbrev = re.sub(r"\s+", "_", source.strip())
        abbrev = abbrev.replace("/", "_").replace("\\", "_")
        return abbrev or "supplier"

    def _attachment_filename(self, payload, partner, kind):
        inv_date = payload.get("invoice_date") or ""
        date_part = str(inv_date)[:10] if inv_date else "unknown-date"
        abbrev = self._supplier_filename_abbrev(partner)
        invoice_no = payload.get("invoice_number") or "unknown"
        suffix = "-receipt" if kind == "receipt" else ""
        return f"{date_part}-{abbrev}-{invoice_no}{suffix}.pdf"

    def _try_unwrap_url(self, url):
        """Hook: providers that use email click-tracking redirect wrappers
        (e.g. Stripe's *.email.stripe.com/CL0/<encoded-target>/...) should
        override this and return the decoded target URL. Return the same
        url unchanged when there is nothing to unwrap."""
        return url

    def _unwrap_url_fully(self, url):
        seen = {url}
        current = url
        for _i in range(5):
            new_url = self._try_unwrap_url(current)
            if not new_url or new_url == current or new_url in seen:
                break
            current = new_url
            seen.add(current)
        return current

    # ------------------------------------------------------------------
    # Generic date/amount/http helpers (usable by every provider)
    # ------------------------------------------------------------------
    @api.model
    def _parse_date_iso(self, value):
        if not value:
            return False
        try:
            parsed = date_parser.parse(str(value))
        except (ValueError, OverflowError):
            return False
        return fields.Date.to_string(parsed.date())

    @api.model
    def _parse_date_epoch(self, value):
        if value in (None, False, ""):
            return False
        try:
            parsed = datetime.fromtimestamp(float(value), tz=timezone.utc).date()
        except (ValueError, OverflowError, OSError):
            return False
        return fields.Date.to_string(parsed) if isinstance(parsed, date) else False

    @api.model
    def _download_binary(self, url, headers=None):
        response = requests.get(
            url, headers=headers, timeout=DEFAULT_TIMEOUT, allow_redirects=True
        )
        response.raise_for_status()
        return response.content

    @api.model
    def _format_period_text(self, start_date, end_date):
        """Localized 'Period from X to Y' suffix; empty if same day.

        ``start_date``/``end_date`` are expected as ISO 'YYYY-MM-DD' strings
        (the format returned by ``_parse_date_iso``/``_parse_date_epoch``).
        """
        if not start_date or not end_date or start_date == end_date:
            return ""
        start = fields.Date.from_string(start_date)
        end = fields.Date.from_string(end_date)
        if not start or not end or start == end:
            return ""
        lang_code = self.env.user.lang or "en_US"
        lang = self.env["res.lang"]._lang_get(lang_code)
        date_format = lang.date_format if lang else "%m/%d/%Y"
        start_str = start.strftime(date_format)
        end_str = end.strftime(date_format)
        return " " + _("Period from %(start)s to %(end)s") % {
            "start": start_str,
            "end": end_str,
        }

    # ------------------------------------------------------------------
    # Provider extension points (overridden by provider addons, each
    # override should call super() first and only handle its own URLs)
    # ------------------------------------------------------------------
    def _get_provider_from_url(self, normalized_url):
        return False

    def _fetch_provider_payload(self, provider, normalized_url):
        raise UserError(_("Unsupported hosted invoice provider: %s") % provider)

    # ------------------------------------------------------------------
    # Supplier / product mapping resolution
    # ------------------------------------------------------------------
    def _find_supplier_mapping(self, company, provider, candidate_keys):
        smo = self.env["account.invoice.import.hosted.supplier.mapping"]
        for key in candidate_keys or []:
            norm = self._normalize_key(key)
            if not norm:
                continue
            mapping = smo.search(
                [
                    ("company_id", "=", company.id),
                    ("provider", "=", provider),
                    ("external_key", "=", norm),
                    ("active", "=", True),
                ],
                limit=1,
            )
            if mapping:
                return mapping
        return smo.browse()

    def _find_product_mapping(self, company, provider, description):
        pmo = self.env["account.invoice.import.hosted.product.mapping"]
        norm = self._normalize_key(description)
        if not norm:
            return pmo.browse()
        return pmo.search(
            [
                ("company_id", "=", company.id),
                ("provider", "=", provider),
                ("external_key", "=", norm),
                ("active", "=", True),
            ],
            limit=1,
        )

    # ------------------------------------------------------------------
    # Main orchestration
    # ------------------------------------------------------------------
    def action_process(self):
        self.process_sources()
        return True

    def action_open_mapping_wizard(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Resolve Missing Mappings"),
            "res_model": "account.invoice.import.hosted.mapping.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_source_id": self.id},
        }

    def process_sources(self):
        for source in self:
            try:
                source._process_one()
            except Exception as exc:  # noqa: BLE001
                logger.exception(
                    "Error while processing hosted invoice source %d", source.id
                )
                source.write(
                    {
                        "state": "error",
                        "error_message": str(exc),
                        "process_date": fields.Datetime.now(),
                    }
                )
        return True

    def _process_one(self):
        self.ensure_one()
        company = self.company_id

        final_url = self._unwrap_url_fully(self.normalized_url or self.source_url)
        final_normalized = self._normalize_url(final_url)
        final_hash = self._hash_url(final_normalized)
        if final_normalized != self.normalized_url:
            self.write(
                {"normalized_url": final_normalized, "source_url_hash": final_hash}
            )

        # 1. URL-level dedup (cheap, no HTTP call)
        existing = self.search(
            [
                ("id", "!=", self.id),
                ("company_id", "=", company.id),
                ("source_url_hash", "=", final_hash),
                ("state", "in", ("processed", "duplicate", "expired")),
            ],
            limit=1,
        )
        if existing:
            new_state = "expired" if existing.state == "expired" else "duplicate"
            self.write(
                {
                    "state": new_state,
                    "move_id": existing.move_id.id if existing.move_id else False,
                    "process_date": fields.Datetime.now(),
                }
            )
            return

        # 2. Determine provider
        provider = self._get_provider_from_url(final_normalized)
        if not provider:
            self.write(
                {
                    "state": "error",
                    "error_message": _(
                        "Could not determine the hosted invoice provider for "
                        "this URL."
                    ),
                    "process_date": fields.Datetime.now(),
                }
            )
            return
        if self.provider != provider:
            self.write({"provider": provider})

        # 3. Fetch canonical payload
        payload = self._fetch_provider_payload(provider, final_normalized) or {}

        # 4. Expired link
        if payload.get("expired"):
            self.write({"state": "expired", "process_date": fields.Datetime.now()})
            return

        # Pre-fill the human-readable invoice fields from the fetched payload
        # so they are visible in the source view even when a supplier/product
        # mapping is still missing. The 'processed' branch below overwrites
        # them later with the authoritative values from the created bill.
        self.write(self._payload_display_vals(payload))

        # 5. Resolve supplier mapping (skipped when the supplier is already
        # known, e.g. an API-driven feed that forces the partner)
        if self.forced_partner_id:
            partner = self.forced_partner_id.commercial_partner_id.with_company(
                company.id
            )
        else:
            candidate_keys = payload.get("supplier_candidates") or []
            supplier_mapping = self._find_supplier_mapping(
                company, provider, candidate_keys
            )
            if not supplier_mapping:
                self.write(
                    {
                        "state": "mapping_required",
                        "supplier_external_key": self._normalize_key(
                            candidate_keys[0] if candidate_keys else ""
                        ),
                        "missing_mapping_info": _(
                            "No supplier mapping found for:\n%s"
                        )
                        % "\n".join(candidate_keys or []),
                        "raw_payload": self._serialize_payload(payload),
                        "process_date": fields.Datetime.now(),
                    }
                )
                return
            partner = supplier_mapping.partner_id.commercial_partner_id.with_company(
                company.id
            )

        # 6. Business-level dedup (invoice number + partner)
        parsed_inv_stub = {
            "type": "in_invoice",
            "invoice_number": payload.get("invoice_number"),
        }
        aiio = self.env["account.invoice.import"]
        existing_move = aiio._invoice_already_exists(
            parsed_inv_stub, partner, company.id
        )
        if existing_move:
            self.write(
                {
                    "state": "duplicate",
                    "partner_id": partner.id,
                    "move_id": existing_move.id,
                    "process_date": fields.Datetime.now(),
                }
            )
            return

        # 7. Resolve product mappings for every (non zero-qty/zero-amount) line
        usable_lines = []
        missing_products = []
        for line in payload.get("lines") or []:
            amount = line.get("amount") or 0.0
            tax_amount = line.get("tax_amount") or 0.0
            # Skip lines whose invoiced value rounds to zero (e.g. usage
            # tiers that were never reached): quantity may still be a tiny
            # non-zero number in that case, only the amount matters here.
            if float_is_zero(amount + tax_amount, precision_digits=2):
                continue
            product_mapping = self._find_product_mapping(
                company, provider, line.get("description")
            )
            if not product_mapping:
                missing_products.append(line.get("description") or "?")
                continue
            usable_lines.append((line, product_mapping.product_id))

        if missing_products:
            self.write(
                {
                    "state": "mapping_required",
                    "supplier_external_key": False,
                    "partner_id": partner.id,
                    "missing_mapping_info": _("No product mapping found for:\n%s")
                    % "\n".join(sorted(set(missing_products))),
                    "raw_payload": self._serialize_payload(payload),
                    "process_date": fields.Datetime.now(),
                }
            )
            return

        # 8. Build the parsed_inv pivot dict and create the bill
        parsed_inv = self._build_parsed_inv(payload, partner, usable_lines)
        import_config = partner._convert_to_import_config(company)
        import_config["single_line"] = False
        invoice = aiio.create_invoice(
            parsed_inv,
            import_config,
            origin=_("Hosted invoice link import (%s)") % provider,
        )

        self.write(
            {
                "state": "processed",
                "partner_id": partner.id,
                "move_id": invoice.id,
                "invoice_number": invoice.ref,
                "invoice_date": invoice.invoice_date,
                "invoice_due_date": invoice.invoice_date_due,
                "currency_id": invoice.currency_id.id,
                "amount_untaxed": invoice.amount_untaxed,
                "amount_total": invoice.amount_total,
                "error_message": False,
                "missing_mapping_info": False,
                "process_date": fields.Datetime.now(),
            }
        )

    def _payload_display_vals(self, payload):
        """Best-effort human-readable invoice preview built from the provider
        payload, so the source view shows what the invoice is even before any
        supplier/product mapping has been resolved.

        Amounts are summed from the payload lines and may slightly differ from
        the final bill (rounding, global discounts); they are overwritten with
        the authoritative move values once the bill is created.
        """
        currency = self.env["res.currency"]
        iso = payload.get("currency_iso")
        if iso:
            currency = currency.with_context(active_test=False).search(
                [("name", "=", iso)], limit=1
            )
        amount_untaxed = 0.0
        amount_total = 0.0
        for line in payload.get("lines") or []:
            amount = line.get("amount") or 0.0
            tax_amount = line.get("tax_amount") or 0.0
            amount_untaxed += amount
            amount_total += amount + tax_amount
        return {
            "invoice_number": payload.get("invoice_number"),
            "invoice_date": payload.get("invoice_date"),
            "invoice_due_date": payload.get("due_date"),
            "currency_id": currency.id or False,
            "amount_untaxed": amount_untaxed,
            "amount_total": amount_total,
        }

    def _build_parsed_inv(self, payload, partner, usable_lines):
        lines = []
        amount_total = 0.0
        for line, product in usable_lines:
            qty = line.get("quantity") or 0.0
            amount = line.get("amount") or 0.0
            tax_amount = line.get("tax_amount") or 0.0
            gross = amount + tax_amount
            price_unit = gross / qty if qty else gross
            amount_total += gross
            period_text = self._format_period_text(
                line.get("period_start"), line.get("period_end")
            )
            name = (product.name or line.get("description") or "") + period_text
            line_vals = {
                "product": {"recordset": product},
                "name": name,
                "qty": qty or 1.0,
                "price_unit": price_unit,
            }
            if line.get("period_start") and line.get("period_end"):
                line_vals["date_start"] = line["period_start"]
                line_vals["date_end"] = line["period_end"]
            lines.append(line_vals)

        attachments = {}
        raw_attachments = payload.get("attachments") or []
        # Always record the invoice PDF before the receipt PDF so the invoice
        # ends up with the smaller ir.attachment database id.
        ordered = sorted(
            raw_attachments,
            key=lambda att: 1 if att.get("kind") == "receipt" else 0,
        )
        for att in ordered:
            content = att.get("content")
            if not content:
                continue
            filename = self._attachment_filename(
                payload, partner, att.get("kind") or "invoice"
            )
            attachments[filename] = base64.b64encode(content).decode("ascii")

        parsed_inv = {
            "type": "in_invoice",
            "partner": {"recordset": partner},
            "currency": {"iso": payload.get("currency_iso")},
            "invoice_number": payload.get("invoice_number"),
            "date": payload.get("invoice_date"),
            "date_due": payload.get("due_date"),
            "amount_total": amount_total,
            "lines": lines,
            "attachments": attachments,
            "chatter_msg": [],
        }
        return parsed_inv

    @api.model
    def _serialize_payload(self, payload):
        # Never persist tokenized download URLs or binary content at rest.
        safe = {
            k: v
            for k, v in payload.items()
            if k not in ("attachments",)
        }
        try:
            return json.dumps(safe, default=str, ensure_ascii=False)
        except (TypeError, ValueError):
            return False
