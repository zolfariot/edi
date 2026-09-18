# Copyright 2026 Akretion France (http://www.akretion.com/)
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
import hashlib
import logging
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from odoo import _, api, fields, models
from odoo.exceptions import UserError

logger = logging.getLogger(__name__)


class AccountInvoiceImportHostedSource(models.Model):
    _name = "account.invoice.import.hosted.source"
    _description = "Hosted Invoice Import Source"
    _order = "id desc"
    _rec_name = "source_url"
    _check_company_auto = True

    company_id = fields.Many2one(
        "res.company", required=True, default=lambda self: self.env.company
    )
    partner_id = fields.Many2one(
        "res.partner",
        required=True,
        ondelete="cascade",
        domain=[("parent_id", "=", False)],
        check_company=True,
    )
    source_url = fields.Char(required=True)
    normalized_url = fields.Char(readonly=True)
    source_url_hash = fields.Char(readonly=True, index=True)
    provider = fields.Char(readonly=True)
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("processed", "Processed"),
            ("duplicate", "Duplicate"),
            ("error", "Error"),
        ],
        default="draft",
        required=True,
        readonly=True,
    )
    move_id = fields.Many2one("account.move", readonly=True, check_company=True)
    invoice_number = fields.Char(readonly=True)
    invoice_date = fields.Date(readonly=True)
    invoice_due_date = fields.Date(readonly=True)
    currency_id = fields.Many2one("res.currency", readonly=True)
    amount_untaxed = fields.Monetary(currency_field="currency_id", readonly=True)
    amount_total = fields.Monetary(currency_field="currency_id", readonly=True)
    process_date = fields.Datetime(readonly=True)
    error_message = fields.Text(readonly=True)

    @api.model_create_multi
    def create(self, vals_list):
        vals_list = [self._prepare_source_vals(vals) for vals in vals_list]
        return super().create(vals_list)

    def write(self, vals):
        if "source_url" in vals:
            vals = self._prepare_source_vals(vals)
        return super().write(vals)

    @api.model
    def _normalize_url(self, raw_url):
        raw_url = (raw_url or "").strip()
        if not raw_url:
            raise UserError(_("Hosted invoice URL cannot be empty."))
        parsed = urlsplit(raw_url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise UserError(
                _(
                    "Hosted invoice URL '%(url)s' is invalid. "
                    "Use a full HTTP(S) URL.",
                    url=raw_url,
                )
            )
        path = parsed.path or "/"
        query = urlencode(sorted(parse_qsl(parsed.query, keep_blank_values=True)))
        normalized = urlunsplit(
            (parsed.scheme.lower(), parsed.netloc.lower(), path, query, "")
        )
        return normalized

    @api.model
    def _prepare_source_vals(self, vals):
        new_vals = dict(vals)
        if "source_url" in new_vals:
            normalized_url = self._normalize_url(new_vals["source_url"])
            new_vals.update(
                {
                    "normalized_url": normalized_url,
                    "source_url_hash": hashlib.sha256(
                        normalized_url.encode("utf-8")
                    ).hexdigest(),
                }
            )
        return new_vals

    def action_process(self):
        self.process_sources()
        return True

    def process_sources(self):
        for source in self:
            source._process_source()

    def _process_source(self):
        self.ensure_one()
        if self.state == "processed":
            return
        duplicate = self._find_duplicate_by_source_hash()
        if duplicate:
            self.write(
                {
                    "state": "duplicate",
                    "move_id": duplicate.move_id.id,
                    "provider": duplicate.provider,
                    "error_message": _(
                        "Duplicate hosted URL already processed on source "
                        "#%(source_id)s.",
                        source_id=duplicate.id,
                    ),
                    "process_date": fields.Datetime.now(),
                }
            )
            return

        provider = self._get_provider_from_url(self.normalized_url)
        if not provider:
            self.write(
                {
                    "state": "error",
                    "provider": False,
                    "error_message": _(
                        "No hosted invoice provider matched URL '%(url)s'.",
                        url=self.source_url,
                    ),
                    "process_date": fields.Datetime.now(),
                }
            )
            return

        try:
            payload = self._fetch_provider_payload(provider, self.normalized_url) or {}
            payload = dict(payload)
            payload["provider"] = provider
            duplicate_invoice = self._find_duplicate_invoice(
                payload.get("invoice_number")
            )
            if duplicate_invoice:
                self.write(
                    self._prepare_source_result_vals(payload)
                    | {
                        "state": "duplicate",
                        "move_id": duplicate_invoice.id,
                        "error_message": _(
                            "Vendor bill '%(bill)s' already exists for invoice ref "
                            "'%(ref)s'.",
                            bill=duplicate_invoice.display_name,
                            ref=payload.get("invoice_number"),
                        ),
                        "process_date": fields.Datetime.now(),
                    }
                )
                return

            parsed_inv = self._prepare_parsed_invoice(payload)
            import_config = (
                self.partner_id.commercial_partner_id._convert_to_import_config(
                    self.company_id
                )
            )
            invoice = self.env["account.invoice.import"].create_invoice(
                parsed_inv,
                import_config=import_config,
                origin=_("Hosted invoice URL import"),
            )
            self.write(
                self._prepare_source_result_vals(payload)
                | {
                    "state": "processed",
                    "move_id": invoice.id,
                    "error_message": False,
                    "process_date": fields.Datetime.now(),
                }
            )
        except Exception as err:
            logger.exception("Hosted invoice processing failed for source %s", self.id)
            self.write(
                {
                    "state": "error",
                    "provider": provider,
                    "error_message": str(err),
                    "process_date": fields.Datetime.now(),
                }
            )

    def _prepare_parsed_invoice(self, payload):
        parsed_inv = {
            "type": "in_invoice",
            "partner": {"recordset": self.partner_id.commercial_partner_id},
            "chatter_msg": [],
        }
        if payload.get("invoice_number"):
            parsed_inv["invoice_number"] = payload["invoice_number"]
        if payload.get("invoice_date"):
            parsed_inv["date"] = fields.Date.to_string(payload["invoice_date"])
        if payload.get("due_date"):
            parsed_inv["date_due"] = fields.Date.to_string(payload["due_date"])
        if payload.get("currency_record"):
            parsed_inv["currency"] = {"recordset": payload["currency_record"]}
        elif payload.get("currency_iso"):
            parsed_inv["currency"] = {"iso": payload["currency_iso"]}
        if payload.get("amount_total") is not None:
            parsed_inv["amount_total"] = payload["amount_total"]
        if payload.get("amount_untaxed") is not None:
            parsed_inv["amount_untaxed"] = payload["amount_untaxed"]
        if payload.get("description"):
            parsed_inv["description"] = payload["description"]

        attachments = {}
        for attachment in payload.get("attachments", []):
            filename = attachment.get("filename")
            content = attachment.get("content")
            if not filename or not content:
                continue
            if isinstance(content, str):
                content = content.encode("utf-8")
            attachments[filename] = base64.b64encode(content)
        if attachments:
            parsed_inv["attachments"] = attachments
        return parsed_inv

    def _prepare_source_result_vals(self, payload):
        vals = {
            "provider": payload.get("provider"),
            "invoice_number": payload.get("invoice_number"),
            "invoice_date": payload.get("invoice_date")
            and fields.Date.to_date(payload["invoice_date"])
            or False,
            "invoice_due_date": payload.get("due_date")
            and fields.Date.to_date(payload["due_date"])
            or False,
            "amount_untaxed": payload.get("amount_untaxed"),
            "amount_total": payload.get("amount_total"),
        }
        if payload.get("currency_record"):
            vals["currency_id"] = payload["currency_record"].id
        elif payload.get("currency_iso"):
            currency = self.env["res.currency"].search(
                [("name", "=", payload["currency_iso"])], limit=1
            )
            vals["currency_id"] = currency.id
        return vals

    def _find_duplicate_by_source_hash(self):
        self.ensure_one()
        return self.search(
            [
                ("id", "!=", self.id),
                ("company_id", "=", self.company_id.id),
                ("source_url_hash", "=", self.source_url_hash),
                ("state", "in", ("processed", "duplicate")),
            ],
            limit=1,
        )

    def _find_duplicate_invoice(self, invoice_number):
        self.ensure_one()
        if not invoice_number:
            return self.env["account.move"]
        return self.env["account.move"].search(
            [
                ("company_id", "=", self.company_id.id),
                (
                    "commercial_partner_id",
                    "=",
                    self.partner_id.commercial_partner_id.id,
                ),
                ("move_type", "in", ("in_invoice", "in_refund")),
                ("ref", "=ilike", invoice_number),
            ],
            limit=1,
        )

    def _get_provider_from_url(self, normalized_url):
        return False

    def _fetch_provider_payload(self, provider, normalized_url):
        raise UserError(
            _(
                "Provider '%(provider)s' is not supported in this database.",
                provider=provider,
            )
        )
