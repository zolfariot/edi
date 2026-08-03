# Copyright 2026 Akretion France (http://www.akretion.com/)
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class AccountInvoiceImportHostedMappingWizardSupplierLine(models.TransientModel):
    _name = "account.invoice.import.hosted.mapping.wizard.supplier.line"
    _description = "Hosted Invoice Import - Missing Supplier Mapping Line"

    wizard_id = fields.Many2one(
        "account.invoice.import.hosted.mapping.wizard"
    )
    external_label = fields.Char(readonly=True)
    partner_id = fields.Many2one("res.partner", string="Supplier")


class AccountInvoiceImportHostedMappingWizardProductLine(models.TransientModel):
    _name = "account.invoice.import.hosted.mapping.wizard.product.line"
    _description = "Hosted Invoice Import - Missing Product Mapping Line"

    wizard_id = fields.Many2one(
        "account.invoice.import.hosted.mapping.wizard"
    )
    external_label = fields.Char(readonly=True)
    product_id = fields.Many2one("product.product")


class AccountInvoiceImportHostedMappingWizard(models.TransientModel):
    _name = "account.invoice.import.hosted.mapping.wizard"
    _description = "Resolve Missing Hosted Invoice Mappings"

    source_id = fields.Many2one(
        "account.invoice.import.hosted.source", required=True
    )
    company_id = fields.Many2one(related="source_id.company_id", readonly=True)
    provider = fields.Selection(related="source_id.provider", readonly=True)
    supplier_line_ids = fields.One2many(
        "account.invoice.import.hosted.mapping.wizard.supplier.line", "wizard_id"
    )
    product_line_ids = fields.One2many(
        "account.invoice.import.hosted.mapping.wizard.product.line", "wizard_id"
    )

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        source_id = self.env.context.get("default_source_id")
        if not source_id:
            return res
        source = self.env["account.invoice.import.hosted.source"].browse(source_id)
        if "supplier_line_ids" in fields_list:
            supplier_lines = []
            if source.supplier_external_key and not source.partner_id:
                supplier_lines.append(
                    (0, 0, {"external_label": source.supplier_external_key})
                )
            res["supplier_line_ids"] = supplier_lines
        if "product_line_ids" in fields_list:
            product_lines = []
            # Once the supplier is resolved, missing_mapping_info (if any)
            # lists missing product descriptions instead (header line + items).
            if source.partner_id and source.missing_mapping_info:
                info_lines = source.missing_mapping_info.splitlines()
                for key in info_lines[1:]:
                    key = key.strip()
                    if key:
                        product_lines.append((0, 0, {"external_label": key}))
            res["product_line_ids"] = product_lines
        return res

    def action_create_mappings_and_reprocess(self):
        self.ensure_one()
        smo = self.env["account.invoice.import.hosted.supplier.mapping"]
        pmo = self.env["account.invoice.import.hosted.product.mapping"]
        for line in self.supplier_line_ids:
            if not line.partner_id:
                raise UserError(
                    _("Please select a supplier for %s.") % line.external_label
                )
            smo.create(
                {
                    "company_id": self.company_id.id,
                    "provider": self.provider,
                    "external_key": line.external_label,
                    "external_label": line.external_label,
                    "partner_id": line.partner_id.id,
                }
            )
        for line in self.product_line_ids:
            if not line.product_id:
                raise UserError(
                    _("Please select a product for %s.") % line.external_label
                )
            pmo.create(
                {
                    "company_id": self.company_id.id,
                    "provider": self.provider,
                    "external_key": line.external_label,
                    "external_label": line.external_label,
                    "product_id": line.product_id.id,
                }
            )

        # Reprocess this source plus any other pending source sharing the
        # same company/provider (they might be unblocked by the new mappings).
        pending = self.env["account.invoice.import.hosted.source"].search(
            [
                ("company_id", "=", self.company_id.id),
                ("provider", "=", self.provider),
                ("state", "in", ("draft", "mapping_required")),
            ]
        )
        pending.process_sources()
        return {
            "type": "ir.actions.act_window",
            "name": _("Hosted Invoice Sources"),
            "res_model": "account.invoice.import.hosted.source",
            "view_mode": "list,form",
            "domain": [("id", "in", pending.ids)],
        }

