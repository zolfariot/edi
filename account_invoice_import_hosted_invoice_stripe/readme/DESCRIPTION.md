Adds support for the Stripe provider to
``account_invoice_import_hosted_invoice``: paste a Stripe hosted invoice
link (as received by e-mail, even wrapped in Stripe's click-tracking
redirect) and the module will use the 2-step hosted invoice API (ephemeral
key) to fetch the invoice JSON, download the invoice/receipt PDF and create
the corresponding vendor bill (once the supplier and products are mapped).
