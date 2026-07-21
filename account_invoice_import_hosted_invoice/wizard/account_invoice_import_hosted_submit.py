# Copyright 2026 Akretion France (http://www.akretion.com/)
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, fields, models
from odoo.exceptions import UserError


class AccountInvoiceImportHostedSubmit(models.TransientModel):
    _name = "account.invoice.import.hosted.submit"
    _description = "Submit Hosted Invoice URLs"

    company_id = fields.Many2one(
        "res.company", required=True, default=lambda self: self.env.company
    )
    partner_id = fields.Many2one(
        "res.partner",
        required=True,
        domain=[("parent_id", "=", False)],
        check_company=True,
    )
    url_text = fields.Text(required=True, string="Hosted Invoice URLs")

    def action_submit_and_process(self):
        self.ensure_one()
        source_model = self.env["account.invoice.import.hosted.source"]
        urls = [
            line.strip() for line in (self.url_text or "").splitlines() if line.strip()
        ]
        if not urls:
            raise UserError(_("Please provide at least one hosted invoice URL."))
        vals_list = [
            {
                "company_id": self.company_id.id,
                "partner_id": self.partner_id.id,
                "source_url": url,
            }
            for url in urls
        ]
        sources = source_model.create(vals_list)
        sources.process_sources()
        action = self.env["ir.actions.actions"]._for_xml_id(
            "account_invoice_import_hosted_invoice.account_invoice_import_hosted_source_action"
        )
        action["domain"] = [("id", "in", sources.ids)]
        return action
