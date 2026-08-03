# Copyright 2026 Akretion France (http://www.akretion.com/)
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import models


class AccountMove(models.Model):
    _inherit = "account.move"

    def unlink(self):
        # Some deployments do not enforce the move_id foreign key at the
        # database level (ondelete policy), so make sure hosted invoice
        # sources pointing to a deleted bill don't keep a dangling
        # reference that would break the source list/form views.
        sources = self.env["account.invoice.import.hosted.source"].search(
            [("move_id", "in", self.ids)]
        )
        if sources:
            sources.write({"move_id": False})
        return super().unlink()
