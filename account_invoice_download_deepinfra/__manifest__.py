# Copyright 2026 Akretion France (http://www.akretion.com/)
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

{
    "name": "Account Invoice Download DeepInfra",
    "version": "18.0.1.0.0",
    "category": "Accounting",
    "license": "AGPL-3",
    "summary": "Auto-download DeepInfra invoices via their Stripe billing portal",
    "author": "Akretion,Odoo Community Association (OCA)",
    "website": "https://github.com/OCA/edi",
    "depends": [
        "account_invoice_download",
        "account_invoice_import_hosted_invoice_stripe",
    ],
    "external_dependencies": {
        "python": ["requests"],
    },
    "data": [
        "views/account_invoice_download_config.xml",
    ],
    "installable": True,
}
