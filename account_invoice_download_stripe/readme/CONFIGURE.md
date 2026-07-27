First, you need to create a Stripe API key. For that, log in to your
[Stripe Dashboard](https://dashboard.stripe.com/), go to *Developers \>
API Keys* and create a new restricted key with *Invoices: Read* permission.
Write down the secret key in a safe place.

In Odoo, go to the menu *Invoicing \> Configuration \> Import Vendor
Bills \> Download Bills* and create a new entry. Select *Stripe* as
*Backend* and set the Stripe API Key.
