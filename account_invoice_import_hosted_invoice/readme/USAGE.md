Go to *Accounting > Configuration > Hosted Invoice Import > Import from
Links* and paste one or several hosted invoice links (one per line, they can
belong to different suppliers). Click on *Submit and Process*.

For each link, the corresponding hosted invoice source record will show one
of the following states:

* **Draft**: not processed yet.
* **Mapping Required**: the supplier or one of the products could not be
  automatically identified. Use the *Resolve Missing Mappings* button to
  create the missing mapping(s) and reprocess.
* **Expired**: the link is no longer valid, nothing was imported.
* **Duplicate**: an invoice with the same number and supplier (or the exact
  same link) already exists in Odoo.
* **Processed**: the vendor bill was created.
* **Error**: an unexpected error occurred, see the error message.

Supplier and product mappings can also be configured in advance from
*Accounting > Configuration > Hosted Invoice Import > Supplier Mappings* /
*Product Mappings*.
