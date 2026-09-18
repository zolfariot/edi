{
    "name": "Account Invoice Import Hosted Invoice",
    "version": "18.0.1.0.0",
    "category": "Accounting",
    "license": "AGPL-3",
    "summary": "Import vendor bills from hosted invoice URLs",
    "author": "Akretion,Odoo Community Association (OCA)",
    "maintainers": ["alexis-via"],
    "website": "https://github.com/OCA/edi",
    "depends": [
        "account_invoice_import",
    ],
    "data": [
        "security/rule.xml",
        "security/ir.model.access.csv",
        "wizard/account_invoice_import_hosted_submit_view.xml",
        "views/account_invoice_import_hosted_source.xml",
    ],
    "installable": True,
}
