To configure this module:

1.  Go to *Invoicing > Configuration > Suppliers Invoice Download* and
    create a new download configuration.
2.  Select the supplier (the partner used for the imported DeepInfra bills)
    and set the *Backend* to **DeepInfra**.
3.  Enter your **DeepInfra API Key** (available from your DeepInfra dashboard).
4.  Optionally set the *Download Method* to **Automatic** together with a
    frequency to let the scheduled action import invoices periodically.

DeepInfra invoices are matched to Odoo products through the *hosted invoice*
product mappings (provider *Stripe*). The first import of a new product will
mark the corresponding hosted invoice source as *Mapping Required*; add the
mapping and re-run to import the bill.
