Adds support for the Withorb provider to
``account_invoice_import_hosted_invoice``: paste a Withorb hosted invoice
link (``https://invoices.withorb.com/view?token=...``) and the module will
fetch the invoice JSON, download the invoice/receipt PDF and create the
corresponding vendor bill (once the supplier and products are mapped).
