# Copyright 2024 Akretion France (http://www.akretion.com/)
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

import base64
import logging

import requests

from odoo import _, fields, models
from odoo.exceptions import UserError

logger = logging.getLogger(__name__)
WITHORB_API_URL = "https://api.withorb.com/v1"
TIMEOUT = 30
# Withorb requires a browser-like user-agent to process requests
WITHORB_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


class AccountInvoiceDownloadConfig(models.Model):
    _inherit = "account.invoice.download.config"

    backend = fields.Selection(
        selection_add=[("withorb", "Withorb")], ondelete={"withorb": "set null"}
    )
    withorb_api_key = fields.Char(string="Withorb API Key")

    def prepare_credentials(self):
        credentials = super().prepare_credentials()
        if self.backend == "withorb":
            credentials = {"api_key": self.withorb_api_key}
        return credentials

    def credentials_stored(self):
        if self.backend == "withorb":
            if self.withorb_api_key:
                return True
            else:
                raise UserError(_("You must set the Withorb API Key."))
        return super().credentials_stored()

    def download(self, credentials, logs):
        if self.backend == "withorb":
            return self.withorb_download(credentials, logs)
        return super().download(credentials, logs)

    def _withorb_invoice_attach_pdf(self, parsed_inv, pdf_url, headers):
        logger.info(
            "Starting to download PDF of Withorb invoice %s dated %s",
            parsed_inv["invoice_number"],
            parsed_inv["date"],
        )
        logger.debug("Withorb invoice download url: %s", pdf_url)
        rpdf = requests.get(pdf_url, headers=headers, timeout=TIMEOUT)
        logger.info("Withorb invoice PDF download HTTP code: %s", rpdf.status_code)
        if rpdf.status_code == 200:
            pdf_b64 = base64.b64encode(rpdf.content).decode("ascii")
            filename = f"withorb_invoice_{parsed_inv['invoice_number']}.pdf"
            parsed_inv["attachments"] = {filename: pdf_b64}
            logger.info(
                "Successfully downloaded PDF of Withorb invoice %s",
                parsed_inv["invoice_number"],
            )
        else:
            logger.warning(
                "Could not download PDF of Withorb invoice %s. HTTP error %d",
                parsed_inv["invoice_number"],
                rpdf.status_code,
            )

    def withorb_download(self, credentials, logs):
        invoices = []
        logger.info(
            "Start to download Withorb invoices with config %s", self.display_name
        )
        auth_scheme = "Bearer"
        headers = {
            "Authorization": f"{auth_scheme} {credentials['api_key']}",
            "User-Agent": WITHORB_USER_AGENT,
        }
        params = {"limit": 500, "status": "issued"}
        if self.download_start_date:
            params["invoice_date[gte]"] = self.download_start_date.isoformat()

        list_url = f"{WITHORB_API_URL}/invoices"
        logger.info("Starting Withorb API query on %s", list_url)
        logger.debug("URL params=%s", params)
        try:
            res = requests.get(
                list_url, headers=headers, params=params, timeout=TIMEOUT
            )
        except Exception as e:
            logs["msg"].append(
                _("Cannot connect to the Withorb API. Error message: '%s'.") % str(e)
            )
            logs["result"] = "failure"
            return []

        if res.status_code != 200:
            logs["msg"].append(
                _(
                    "Withorb API returned HTTP error %(status)d. "
                    "Response: %(response)s",
                    status=res.status_code,
                    response=res.text,
                )
            )
            logs["result"] = "failure"
            return []

        res_json = res.json()
        logger.debug("Result of Withorb invoice list: %s", res_json)

        for inv in res_json.get("data", []):
            inv_number = inv.get("id")
            if not inv_number:
                logger.info("Skipping Withorb invoice without ID")
                continue
            currency_code = (inv.get("currency") or "").upper()
            amount_due = float(inv.get("amount_due", 0) or 0)
            subtotal = float(inv.get("subtotal", 0) or 0)
            inv_date = inv.get("invoice_date") or inv.get("created_at", "")[:10]
            due_date = inv.get("due_date")
            parsed_inv = {
                "invoice_number": inv_number,
                "currency": {"iso": currency_code},
                "date": inv_date or False,
                "date_due": due_date or False,
                "amount_untaxed": subtotal,
                "amount_total": amount_due,
            }
            pdf_url = inv.get("invoice_pdf")
            if pdf_url:
                self._withorb_invoice_attach_pdf(parsed_inv, pdf_url, headers)

            logger.debug("Final parsed_inv=%s", parsed_inv)
            invoices.append(parsed_inv)

        return invoices
