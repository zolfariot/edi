# Copyright 2026 Akretion France (http://www.akretion.com/)
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import models


class ResPartner(models.Model):
    _inherit = "res.partner"

    def _commercial_partner_update_import_config(self, import_config):
        res = super()._commercial_partner_update_import_config(import_config)
        # Every hosted invoice line is mapped to a product: never shrink them,
        # whatever the supplier's "Shrink Invoice Lines" setting.
        if "hosted_products" in import_config:
            import_config["shrink_lines"] = False
        return res
