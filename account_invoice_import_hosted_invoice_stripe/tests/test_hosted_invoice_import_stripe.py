# Copyright 2026 Akretion France (http://www.akretion.com/)
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from unittest.mock import Mock, patch

from odoo import Command
from odoo.tests.common import TransactionCase


class TestHostedInvoiceImportStripe(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.ref("base.main_company")
        cls.expense_account = cls.env["account.account"].create(
            {
                "code": "612HIS",
                "name": "Hosted invoice import expense",
                "account_type": "expense",
                "company_ids": [Command.set([cls.company.id])],
            }
        )
        cls.income_account = cls.env["account.account"].create(
            {
                "code": "707HIS",
                "name": "Hosted invoice import income",
                "account_type": "income",
                "company_ids": [Command.set([cls.company.id])],
            }
        )
        cls.product = (
            cls.env["product.product"]
            .with_company(cls.company.id)
            .create(
                {
                    "name": "Hosted invoice import product",
                    "default_code": "HIS-PROD",
                    "property_account_income_id": cls.income_account.id,
                    "property_account_expense_id": cls.expense_account.id,
                }
            )
        )
        cls.partner = cls.env["res.partner"].create(
            {
                "name": "Hosted Invoice Vendor",
                "is_company": True,
                "invoice_import_product_id": cls.product.id,
            }
        )

    def _response(self, url, text=None, content=None):
        response = Mock()
        response.url = url
        response.text = text or ""
        response.content = content or b""
        response.raise_for_status = Mock()
        return response

    def test_stripe_happy_path(self):
        html = """
            <html>
                <body>
                    <a href=\"/invoice.pdf\">Download invoice PDF</a>
                    <a href=\"/receipt.pdf\">Download receipt PDF</a>
                    <div>Invoice number: INV-2026-0001</div>
                    <div>Invoice date: 2026-07-15</div>
                    <div>Due date: 2026-07-30</div>
                    <div>Subtotal: USD 10.00</div>
                    <div>Total: USD 12.00</div>
                </body>
            </html>
        """
        with patch(
            "odoo.addons.account_invoice_import_hosted_invoice_stripe.models.account_invoice_import_hosted_source.requests.get"
        ) as mock_get:
            mock_get.side_effect = [
                self._response(
                    "https://invoice.stripe.com/i/acct/live_123?s=ap",
                    text=html,
                ),
                self._response(
                    "https://invoice.stripe.com/invoice.pdf", content=b"invoice-pdf"
                ),
                self._response(
                    "https://invoice.stripe.com/receipt.pdf", content=b"receipt-pdf"
                ),
            ]
            wizard = self.env["account.invoice.import.hosted.submit"].create(
                {
                    "company_id": self.company.id,
                    "partner_id": self.partner.id,
                    "url_text": "https://invoice.stripe.com/i/acct/live_123?s=ap",
                }
            )
            wizard.action_submit_and_process()

        source = self.env["account.invoice.import.hosted.source"].search([], limit=1)
        self.assertEqual(source.state, "processed")
        self.assertEqual(source.provider, "stripe")
        self.assertEqual(source.invoice_number, "INV-2026-0001")
        self.assertTrue(source.move_id)
        self.assertEqual(source.move_id.ref, "INV-2026-0001")

        attachments = self.env["ir.attachment"].search(
            [
                ("res_model", "=", "account.move"),
                ("res_id", "=", source.move_id.id),
                ("name", "in", ("invoice.pdf", "receipt.pdf")),
            ]
        )
        self.assertEqual(len(attachments), 2)

    def test_stripe_duplicate_on_invoice_ref(self):
        html_1 = """
            <html><body>
                <a href=\"/invoice-a.pdf\">Invoice PDF</a>
                <div>Invoice number: INV-2026-DUPL</div>
                <div>Total: USD 20.00</div>
            </body></html>
        """
        html_2 = """
            <html><body>
                <a href=\"/invoice-b.pdf\">Invoice PDF</a>
                <div>Invoice number: INV-2026-DUPL</div>
                <div>Total: USD 20.00</div>
            </body></html>
        """
        with patch(
            "odoo.addons.account_invoice_import_hosted_invoice_stripe.models.account_invoice_import_hosted_source.requests.get"
        ) as mock_get:
            mock_get.side_effect = [
                self._response(
                    "https://invoice.stripe.com/i/acct/live_a?s=ap",
                    text=html_1,
                ),
                self._response(
                    "https://invoice.stripe.com/invoice-a.pdf", content=b"invoice-a"
                ),
                self._response(
                    "https://invoice.stripe.com/i/acct/live_b?s=ap",
                    text=html_2,
                ),
                self._response(
                    "https://invoice.stripe.com/invoice-b.pdf", content=b"invoice-b"
                ),
            ]
            wizard = self.env["account.invoice.import.hosted.submit"].create(
                {
                    "company_id": self.company.id,
                    "partner_id": self.partner.id,
                    "url_text": "\n".join(
                        [
                            "https://invoice.stripe.com/i/acct/live_a?s=ap",
                            "https://invoice.stripe.com/i/acct/live_b?s=ap",
                        ]
                    ),
                }
            )
            wizard.action_submit_and_process()

        sources = self.env["account.invoice.import.hosted.source"].search(
            [], order="id asc"
        )
        self.assertEqual(len(sources), 2)
        self.assertEqual(sources[0].state, "processed")
        self.assertEqual(sources[1].state, "duplicate")
        self.assertEqual(sources[1].move_id.id, sources[0].move_id.id)
