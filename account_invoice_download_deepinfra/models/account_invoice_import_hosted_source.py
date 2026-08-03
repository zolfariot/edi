# Copyright 2026 Akretion France (http://www.akretion.com/)
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import fields, models


class AccountInvoiceImportHostedSource(models.Model):
    _inherit = "account.invoice.import.hosted.source"

    download_config_id = fields.Many2one(
        "account.invoice.download.config",
        string="Download Configuration",
        readonly=True,
        copy=False,
        ondelete="set null",
        index=True,
        help="Download configuration that discovered this invoice link.",
    )
