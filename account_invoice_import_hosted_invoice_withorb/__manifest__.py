# Copyright 2026 Akretion France (http://www.akretion.com/)
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

{
    "name": "Account Invoice Import Hosted Invoice Withorb",
    "version": "18.0.1.0.0",
    "category": "Accounting & Finance",
    "license": "AGPL-3",
    "summary": "Import vendor bills from Withorb hosted invoice links",
    "author": "Akretion,Odoo Community Association (OCA)",
    "website": "https://github.com/OCA/edi",
    "depends": [
        "account_invoice_import_hosted_invoice",
    ],
    "external_dependencies": {
        "python": ["requests"],
    },
    "data": [],
    "installable": True,
}
