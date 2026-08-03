This module automatically downloads
[DeepInfra](https://deepinfra.com/) invoices and imports them as vendor
bills in Odoo.

DeepInfra bills through Stripe. This module uses your DeepInfra API key to
obtain a fresh Stripe customer billing portal URL, lists every invoice of
that portal, and imports each one through the standard *hosted invoice*
pipeline (line detail, product mapping and PDF/receipt attachments).

Because the DeepInfra API key never expires, a fresh Stripe portal session is
requested on every run, transparently working around the short lifetime of
Stripe portal URLs.
