# Copyright 2026 Akretion France (http://www.akretion.com/)
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import logging
from urllib.parse import parse_qs, urlparse

import requests

from odoo import fields, models

logger = logging.getLogger(__name__)

WITHORB_API_URL = "https://invoices.withorb.com/api/v1/invoice_from_link"
# Withorb rejects requests without a browser-like User-Agent header.
WITHORB_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)
TIMEOUT = 30


class AccountInvoiceImportHostedSource(models.Model):
    _inherit = "account.invoice.import.hosted.source"

    provider = fields.Selection(selection_add=[("withorb", "Withorb")])

    def _get_provider_from_url(self, normalized_url):
        provider = super()._get_provider_from_url(normalized_url)
        if provider:
            return provider
        parsed = urlparse(normalized_url)
        if (
            parsed.scheme == "https"
            and (parsed.hostname or "").lower() == "invoices.withorb.com"
            and parsed.path.rstrip("/") == "/view"
            and parse_qs(parsed.query).get("token")
        ):
            return "withorb"
        return False

    def _fetch_provider_payload(self, provider, normalized_url):
        if provider != "withorb":
            return super()._fetch_provider_payload(provider, normalized_url)
        return self._withorb_fetch_payload(normalized_url)

    def _withorb_headers(self):
        return {
            "User-Agent": WITHORB_USER_AGENT,
            "Accept": "application/json",
        }

    def _withorb_fetch_payload(self, normalized_url):
        parsed = urlparse(normalized_url)
        token = parse_qs(parsed.query).get("token", [None])[0]
        headers = self._withorb_headers()
        response = requests.get(
            WITHORB_API_URL, params={"token": token}, headers=headers, timeout=TIMEOUT
        )
        if response.status_code in (404, 410):
            return {"expired": True}
        response.raise_for_status()
        data = response.json()
        invoice = data.get("invoice") or {}
        return {
            "supplier_candidates": self._withorb_supplier_candidates(data, invoice),
            "invoice_number": invoice.get("invoice_number"),
            "invoice_date": self._parse_date_iso(invoice.get("invoice_date")),
            "due_date": self._parse_date_iso(invoice.get("due_date")),
            "currency_iso": (
                invoice.get("invoicing_pricing_unit") or {}
            ).get("short_name"),
            "lines": self._withorb_build_lines(invoice),
            "attachments": self._withorb_download_attachments(invoice, headers),
        }

    def _withorb_supplier_candidates(self, data, invoice):
        account_settings = invoice.get("account_settings") or {}
        candidates = [
            account_settings.get("company_name"),
            data.get("account_name"),
            (account_settings.get("invoice_delivery") or {}).get("reply_to"),
        ]
        return [c for c in candidates if c]

    def _withorb_build_lines(self, invoice):
        lines = []
        for item in invoice.get("line_items") or []:
            try:
                amount = float(item.get("subtotal", item.get("amount", 0)) or 0)
            except (TypeError, ValueError):
                amount = 0.0
            tax_amount = 0.0
            for tax in item.get("tax_amounts") or []:
                try:
                    tax_amount += float(tax.get("tax_amount") or 0)
                except (TypeError, ValueError):
                    continue
            lines.append(
                {
                    "description": item.get("name"),
                    "quantity": item.get("quantity") or 0.0,
                    "amount": amount,
                    "tax_amount": tax_amount,
                    "period_start": self._parse_date_iso(item.get("start_date")),
                    "period_end": self._parse_date_iso(item.get("end_date")),
                }
            )
        return lines

    def _withorb_download_attachments(self, invoice, headers):
        attachments = []
        for kind, key in (("invoice", "invoice_pdf"), ("receipt", "receipt_pdf")):
            url = invoice.get(key)
            if not url:
                continue
            try:
                content = self._download_binary(url, headers=headers)
            except Exception:  # noqa: BLE001
                logger.warning(
                    "Could not download Withorb %s PDF, skipping.", kind, exc_info=True
                )
                continue
            attachments.append({"kind": kind, "content": content})
        return attachments
