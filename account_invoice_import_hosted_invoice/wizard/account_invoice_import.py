# Copyright 2026 Akretion France (http://www.akretion.com/)
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import api, models


class AccountInvoiceImport(models.TransientModel):
    _inherit = "account.invoice.import"

    @api.model
    def _hosted_line_product(self, line_dict, import_config):
        return (import_config.get("hosted_products") or {}).get(line_dict["BT-126"])

    @api.model
    def _match_product(self, line_dict, parsed_inv, import_config):
        product = self._hosted_line_product(line_dict, import_config)
        if product:
            self._info_log(
                import_config,
                f"Invoice line {line_dict['BT-126']}: product "
                f"{product.display_name} matched via hosted invoice product mapping",
            )
            return product
        return super()._match_product(line_dict, parsed_inv, import_config)

    @api.model
    def _match_vat_tax_bg25(self, line_dict, parsed_inv, import_config):
        # Hosted invoices carry little tax information (often just a reverse
        # charge flag), so the vendor taxes configured on the mapped product
        # win. The VAT category/rate sent by the provider is only a fallback.
        product = self._hosted_line_product(line_dict, import_config)
        if product:
            company = import_config["company"]
            taxes = product.with_company(company).supplier_taxes_id.filtered(
                lambda tax: tax.company_id == company
            )
            if taxes:
                cpartner = import_config.get("commercial_partner")
                fp = cpartner and cpartner.property_account_position_id
                if fp:
                    taxes = fp.map_tax(taxes)
                self._info_log(
                    import_config,
                    f"Invoice line {line_dict['BT-126']}: taxes "
                    f"{', '.join(taxes.mapped('display_name'))} taken from "
                    "the vendor taxes of the mapped product",
                )
                return taxes
        return super()._match_vat_tax_bg25(line_dict, parsed_inv, import_config)
