This module lets you import vendor bills from hosted invoice links (for
example links received by e-mail from Stripe or Withorb), instead of
uploading a PDF file.

It provides the generic infrastructure (link ingestion, dedup, supplier and
product mapping, mapping-required workflow) used by the provider-specific
modules such as ``account_invoice_import_hosted_invoice_stripe`` and
``account_invoice_import_hosted_invoice_withorb``.
