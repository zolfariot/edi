# Copyright 2026 Akretion France (http://www.akretion.com/)
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

{
    "name": "Account Invoice Import Hosted Invoice",
    "version": "18.0.1.0.0",
    "category": "Accounting & Finance",
    "license": "AGPL-3",
    "summary": "Import vendor bills from hosted invoice links (Stripe, Withorb, ...)",
    "author": "Akretion,Odoo Community Association (OCA)",
    "website": "https://github.com/OCA/edi",
    "depends": [
        "account_invoice_import",
        "base_business_document_import",
    ],
    "external_dependencies": {
        "python": ["requests", "dateutil"],
    },
    "data": [
        "security/ir.model.access.csv",
        "security/rule.xml",
        "views/account_invoice_import_hosted_source_view.xml",
        "views/account_invoice_import_hosted_supplier_mapping_view.xml",
        "views/account_invoice_import_hosted_product_mapping_view.xml",
        "wizard/account_invoice_import_hosted_submit_view.xml",
        "wizard/account_invoice_import_hosted_mapping_wizard_view.xml",
        "views/menu.xml",
    ],
    "installable": True,
}
