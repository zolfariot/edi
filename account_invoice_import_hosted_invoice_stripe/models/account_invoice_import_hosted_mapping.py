# Copyright 2026 Akretion France (http://www.akretion.com/)
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import fields, models


class AccountInvoiceImportHostedSupplierMapping(models.Model):
    _inherit = "account.invoice.import.hosted.supplier.mapping"

    provider = fields.Selection(selection_add=[("stripe", "Stripe")])


class AccountInvoiceImportHostedProductMapping(models.Model):
    _inherit = "account.invoice.import.hosted.product.mapping"

    provider = fields.Selection(selection_add=[("stripe", "Stripe")])
