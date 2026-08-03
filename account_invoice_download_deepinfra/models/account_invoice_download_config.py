# Copyright 2026 Akretion France (http://www.akretion.com/)
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import logging
from collections import Counter

import requests

from odoo import _, fields, models
from odoo.exceptions import UserError

from odoo.addons.account_invoice_import_hosted_invoice_stripe.models.account_invoice_import_hosted_source import (  # noqa: E501
    StripePortalExpired,
)

logger = logging.getLogger(__name__)

DEEPINFRA_BILLING_PORTAL_URL = "https://api.deepinfra.com/payment/billing-portal"
TIMEOUT = 30


class AccountInvoiceDownloadConfig(models.Model):
    _inherit = "account.invoice.download.config"

    backend = fields.Selection(
        selection_add=[("deepinfra", "DeepInfra")],
        ondelete={"deepinfra": "set null"},
    )
    deepinfra_api_key = fields.Char(string="DeepInfra API Key")
    hosted_source_ids = fields.One2many(
        "account.invoice.import.hosted.source",
        "download_config_id",
        string="Hosted Invoice Sources",
        readonly=True,
    )
    hosted_source_count = fields.Integer(
        compute="_compute_hosted_source_count", string="# Hosted Sources"
    )

    def _compute_hosted_source_count(self):
        source_model = self.env["account.invoice.import.hosted.source"]
        for config in self:
            config.hosted_source_count = source_model.search_count(
                [("download_config_id", "=", config.id)]
            )

    def prepare_credentials(self):
        credentials = super().prepare_credentials()
        if self.backend == "deepinfra":
            credentials = {"api_key": self.deepinfra_api_key}
        return credentials

    def credentials_stored(self):
        if self.backend == "deepinfra":
            if self.deepinfra_api_key:
                return True
            raise UserError(_("You must set the DeepInfra API Key."))
        return super().credentials_stored()

    def run(self, credentials):
        self.ensure_one()
        if self.backend == "deepinfra":
            return self._deepinfra_run(credentials)
        return super().run(credentials)

    def action_view_hosted_sources(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Hosted Invoice Sources"),
            "res_model": "account.invoice.import.hosted.source",
            "view_mode": "list,form",
            "domain": [("download_config_id", "=", self.id)],
        }

    # ------------------------------------------------------------------
    # DeepInfra implementation
    # ------------------------------------------------------------------
    def _deepinfra_get_portal_url(self, api_key):
        """Fetch a fresh Stripe billing portal URL from the DeepInfra API.

        The DeepInfra API key never expires, so this can be called again to
        recover from an expired Stripe portal session.
        """
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
        }
        try:
            response = requests.get(
                DEEPINFRA_BILLING_PORTAL_URL, headers=headers, timeout=TIMEOUT
            )
        except requests.RequestException as exc:
            raise UserError(
                _("Could not reach the DeepInfra API: %s") % exc
            ) from exc
        response.raise_for_status()
        portal_url = (response.json() or {}).get("url")
        if not portal_url:
            raise UserError(
                _("DeepInfra did not return a billing portal URL.")
            )
        return portal_url

    def _deepinfra_list_invoices(self, api_key):
        """Return the list of hosted invoice URLs, re-fetching the portal URL
        once if the Stripe session turns out to be expired."""
        source_model = self.env["account.invoice.import.hosted.source"]
        since_date = (
            fields.Date.to_string(self.download_start_date)
            if self.download_start_date
            else None
        )
        for attempt in range(2):
            portal_url = self._deepinfra_get_portal_url(api_key)
            try:
                return source_model._stripe_portal_list_invoice_urls(
                    portal_url, since_date=since_date
                )
            except StripePortalExpired:
                logger.info(
                    "DeepInfra Stripe portal session expired (attempt %d), "
                    "re-fetching a fresh portal URL.",
                    attempt + 1,
                )
        raise UserError(
            _(
                "The DeepInfra Stripe billing portal session keeps expiring; "
                "could not list invoices."
            )
        )

    def _deepinfra_upsert_sources(self, invoices):
        """Create (or reuse) one hosted invoice source per discovered URL.

        Already-processed / duplicate sources are left untouched so re-runs
        stay idempotent; pending ones (draft / mapping_required / error) are
        re-attached to this config and returned for reprocessing.
        """
        source_model = self.env["account.invoice.import.hosted.source"]
        company = self.company_id
        partner = self.partner_id
        by_hash = {}
        for inv in invoices:
            normalized = source_model._normalize_url(inv["hosted_invoice_url"])
            by_hash[source_model._hash_url(normalized)] = inv["hosted_invoice_url"]
        existing = source_model.search(
            [
                ("company_id", "=", company.id),
                ("source_url_hash", "in", list(by_hash.keys())),
            ]
        )
        existing_by_hash = {src.source_url_hash: src for src in existing}
        sources = source_model.browse()
        for url_hash, url in by_hash.items():
            src = existing_by_hash.get(url_hash)
            if src:
                if src.state in ("processed", "duplicate"):
                    continue
                src.write(
                    {
                        "forced_partner_id": partner.id,
                        "download_config_id": self.id,
                    }
                )
            else:
                src = source_model.create(
                    {
                        "company_id": company.id,
                        "source_url": url,
                        "forced_partner_id": partner.id,
                        "download_config_id": self.id,
                    }
                )
            sources |= src
        return sources

    def _deepinfra_run(self, credentials):
        self.ensure_one()
        logger.info("Start DeepInfra invoice download %s", self.display_name)
        api_key = credentials.get("api_key")
        logs = {"msg": [], "result": "success"}
        move_ids = []
        try:
            invoices = self._deepinfra_list_invoices(api_key)
        except Exception as exc:  # noqa: BLE001
            logger.exception("DeepInfra invoice listing failed")
            logs["msg"].append(
                _("Failed to list DeepInfra invoices. Error: %s.") % exc
            )
            logs["result"] = "failure"
            invoices = []

        sources = self._deepinfra_upsert_sources(invoices) if invoices else None
        if sources:
            sources.process_sources()
            move_ids, extra_msg, has_error = self._deepinfra_summarize(sources)
            logs["msg"].extend(extra_msg)
            if has_error:
                logs["result"] = "failure"
        elif logs["result"] == "success":
            logs["msg"].append(_("No new invoice found."))

        if logs["result"] == "success":
            self.last_run = fields.Date.context_today(self)
        log = self.env["account.invoice.download.log"].create(
            {
                "download_config_id": self.id,
                "message": "\n".join(logs["msg"]),
                "invoice_count": len(move_ids),
                "result": logs["result"],
            }
        )
        logger.info(
            "End of DeepInfra invoice download %s. Created invoices: %s",
            self.display_name,
            move_ids,
        )
        return (move_ids, log.id)

    def _deepinfra_summarize(self, sources):
        """Build the per-run log lines from the resulting source states."""
        counts = Counter(sources.mapped("state"))
        move_ids = [
            src.move_id.id
            for src in sources
            if src.state == "processed" and src.move_id
        ]
        msg = []
        if counts.get("processed"):
            msg.append(
                _("%d invoice(s) imported as vendor bills.")
                % counts["processed"]
            )
        if counts.get("duplicate"):
            msg.append(
                _("%d invoice(s) skipped (already in Odoo).")
                % counts["duplicate"]
            )
        if counts.get("expired"):
            msg.append(_("%d invoice link(s) expired.") % counts["expired"])
        if counts.get("mapping_required"):
            msg.append(
                _(
                    "%d invoice(s) need a supplier/product mapping before they "
                    "can be imported. Open the related Hosted Invoice Sources "
                    "and use the mapping wizard, then re-run."
                )
                % counts["mapping_required"]
            )
        if counts.get("error"):
            errors = [
                _("- %(url)s: %(err)s")
                % {"url": src.source_url, "err": src.error_message}
                for src in sources
                if src.state == "error"
            ]
            msg.append(
                _("%d invoice(s) failed:\n%s")
                % (counts["error"], "\n".join(errors))
            )
        has_error = bool(counts.get("error"))
        return move_ids, msg, has_error
