# Copyright 2026 Akretion France (http://www.akretion.com/)
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import api, fields, models


class AccountInvoiceImportHostedProductMapping(models.Model):
    _name = "account.invoice.import.hosted.product.mapping"
    _description = "Hosted Invoice Import - Product Mapping"
    _rec_name = "external_label"

    company_id = fields.Many2one(
        "res.company", required=True, default=lambda self: self.env.company
    )
    provider = fields.Selection([])
    external_key = fields.Char(
        required=True,
        help="Normalized line/product description coming from the provider "
        "used to recognize this product automatically.",
    )
    external_label = fields.Char(
        help="Human readable label of the raw line description (not "
        "normalized), for display purposes only."
    )
    product_id = fields.Many2one(
        "product.product", required=True, ondelete="restrict"
    )
    active = fields.Boolean(default=True)

    _sql_constraints = [
        (
            "external_key_uniq",
            "unique(company_id, provider, external_key)",
            "A mapping already exists for this provider and identifying key "
            "in this company.",
        ),
    ]

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("external_key"):
                vals["external_key"] = self.env[
                    "account.invoice.import.hosted.source"
                ]._normalize_key(vals["external_key"])
        return super().create(vals_list)

    def write(self, vals):
        if vals.get("external_key"):
            vals["external_key"] = self.env[
                "account.invoice.import.hosted.source"
            ]._normalize_key(vals["external_key"])
        return super().write(vals)
