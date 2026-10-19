# Copyright 2026 Akretion France (http://www.akretion.com/)
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, fields, models
from odoo.exceptions import UserError


class AccountInvoiceImportHostedSubmit(models.TransientModel):
    _name = "account.invoice.import.hosted.submit"
    _description = "Submit Hosted Invoice Links"

    company_id = fields.Many2one(
        "res.company", required=True, default=lambda self: self.env.company
    )
    url_text = fields.Text(
        string="Invoice Links",
        required=True,
        help="Paste one hosted invoice link per line. Each link can belong "
        "to a different supplier: the supplier is auto-detected from the "
        "invoice data using the supplier mappings.",
    )

    def action_submit_and_process(self):
        self.ensure_one()
        urls = [
            line.strip() for line in (self.url_text or "").splitlines() if line.strip()
        ]
        if not urls:
            raise UserError(_("Please paste at least one invoice link."))

        hso = self.env["account.invoice.import.hosted.source"]
        sources = hso.browse()
        for url in urls:
            normalized = hso._normalize_url(url)
            url_hash = hso._hash_url(normalized)
            existing = hso.search(
                [
                    ("company_id", "=", self.company_id.id),
                    ("source_url_hash", "=", url_hash),
                ],
                limit=1,
            )
            if existing:
                # Reuse the same row: if already resolved, nothing to do; if
                # still pending (draft/error/mapping_required), it will be
                # (re)processed below.
                sources |= existing
                continue
            sources |= hso.create(
                {"company_id": self.company_id.id, "source_url": url}
            )
        sources.filtered(lambda s: s.state in ("draft", "error", "mapping_required")).process_sources()
        return {
            "type": "ir.actions.act_window",
            "name": _("Hosted Invoice Sources"),
            "res_model": "account.invoice.import.hosted.source",
            "view_mode": "list,form",
            "domain": [("id", "in", sources.ids)],
        }
