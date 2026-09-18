# Copyright 2024 Akretion France (http://www.akretion.com/)
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

{
    "name": "Account Invoice Download Withorb",
    "version": "18.0.1.0.0",
    "category": "Accounting",
    "license": "AGPL-3",
    "summary": "Get Withorb Invoices via the API",
    "author": "Akretion,Odoo Community Association (OCA)",
    "website": "https://github.com/OCA/edi",
    "depends": ["account_invoice_download"],
    "data": [
        "views/account_invoice_download_config.xml",
    ],
    "demo": ["demo/withorb.xml"],
    "installable": True,
    "application": True,
}
