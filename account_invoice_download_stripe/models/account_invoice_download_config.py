# Copyright 2024 Akretion France (http://www.akretion.com/)
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

import base64
import datetime as dt
import logging

import requests

from odoo import _, fields, models
from odoo.exceptions import UserError

logger = logging.getLogger(__name__)
STRIPE_API_URL = "https://api.stripe.com/v1"
TIMEOUT = 30


class AccountInvoiceDownloadConfig(models.Model):
    _inherit = "account.invoice.download.config"

    backend = fields.Selection(
        selection_add=[("stripe", "Stripe")], ondelete={"stripe": "set null"}
    )
    stripe_api_key = fields.Char(string="Stripe API Key")

    def prepare_credentials(self):
        credentials = super().prepare_credentials()
        if self.backend == "stripe":
            credentials = {"api_key": self.stripe_api_key}
        return credentials

    def credentials_stored(self):
        if self.backend == "stripe":
            if self.stripe_api_key:
                return True
            else:
                raise UserError(_("You must set the Stripe API Key."))
        return super().credentials_stored()

    def download(self, credentials, logs):
        if self.backend == "stripe":
            return self.stripe_download(credentials, logs)
        return super().download(credentials, logs)

    def _stripe_invoice_attach_pdf(self, parsed_inv, pdf_url, headers):
        logger.info(
            "Starting to download PDF of Stripe invoice %s dated %s",
            parsed_inv["invoice_number"],
            parsed_inv["date"],
        )
        logger.debug("Stripe invoice download url: %s", pdf_url)
        rpdf = requests.get(pdf_url, headers=headers, timeout=TIMEOUT)
        logger.info("Stripe invoice PDF download HTTP code: %s", rpdf.status_code)
        if rpdf.status_code == 200:
            pdf_b64 = base64.b64encode(rpdf.content).decode("ascii")
            filename = f"stripe_invoice_{parsed_inv['invoice_number']}.pdf"
            parsed_inv["attachments"] = {filename: pdf_b64}
            logger.info(
                "Successfully downloaded PDF of Stripe invoice %s",
                parsed_inv["invoice_number"],
            )
        else:
            logger.warning(
                "Could not download PDF of Stripe invoice %s. HTTP error %d",
                parsed_inv["invoice_number"],
                rpdf.status_code,
            )

    def stripe_download(self, credentials, logs):
        invoices = []
        logger.info(
            "Start to download Stripe invoices with config %s", self.display_name
        )
        auth_scheme = "Bearer"
        headers = {
            "Authorization": f"{auth_scheme} {credentials['api_key']}",
        }
        params = {"limit": 100, "status": "paid"}
        if self.download_start_date:
            start_ts = int(
                dt.datetime.combine(
                    self.download_start_date, dt.time.min
                ).timestamp()
            )
            params["created[gte]"] = start_ts

        list_url = f"{STRIPE_API_URL}/invoices"
        logger.info("Starting Stripe API query on %s", list_url)
        logger.debug("URL params=%s", params)
        try:
            res = requests.get(
                list_url, headers=headers, params=params, timeout=TIMEOUT
            )
        except Exception as e:
            logs["msg"].append(
                _("Cannot connect to the Stripe API. Error message: '%s'.") % str(e)
            )
            logs["result"] = "failure"
            return []

        if res.status_code != 200:
            logs["msg"].append(
                _(
                    "Stripe API returned HTTP error %(status)d. "
                    "Response: %(response)s",
                    status=res.status_code,
                    response=res.text,
                )
            )
            logs["result"] = "failure"
            return []

        res_json = res.json()
        logger.debug("Result of Stripe invoice list: %s", res_json)

        for inv in res_json.get("data", []):
            if not inv.get("number"):
                logger.info(
                    "Skipping Stripe invoice ID %s because it has no number",
                    inv.get("id"),
                )
                continue
            currency_code = (inv.get("currency") or "").upper()
            amount_due = inv.get("amount_due", 0) / 100.0
            subtotal = inv.get("subtotal", 0) / 100.0
            created_ts = inv.get("created")
            due_ts = inv.get("due_date")
            inv_date = (
                dt.datetime.fromtimestamp(created_ts, tz=dt.timezone.utc)
                .date()
                .isoformat()
                if created_ts
                else False
            )
            due_date = (
                dt.datetime.fromtimestamp(due_ts, tz=dt.timezone.utc)
                .date()
                .isoformat()
                if due_ts
                else False
            )
            parsed_inv = {
                "invoice_number": inv["number"],
                "currency": {"iso": currency_code},
                "date": inv_date,
                "date_due": due_date,
                "amount_untaxed": subtotal,
                "amount_total": amount_due,
            }
            pdf_url = inv.get("invoice_pdf")
            if pdf_url:
                self._stripe_invoice_attach_pdf(parsed_inv, pdf_url, headers)

            logger.debug("Final parsed_inv=%s", parsed_inv)
            invoices.append(parsed_inv)

        return invoices
