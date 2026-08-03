# Copyright 2026 Akretion France (http://www.akretion.com/)
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from unittest.mock import Mock, patch

from odoo import Command
from odoo.tests.common import TransactionCase

MODULE = (
    "odoo.addons.account_invoice_import_hosted_invoice_stripe.models"
    ".account_invoice_import_hosted_source"
)

RAW_URL = "https://pay.stripe.com/invoice/acct_1MExQ9BjIQrRQnux/live_secret123/pdf?s=em"
WRAPPED_URL = (
    "https://59.email.stripe.com/CL0/"
    "https:%2F%2Fpay.stripe.com%2Finvoice%2Facct_1MExQ9BjIQrRQnux%2F"
    "live_secret123%2Fpdf%3Fs=em"
    "/1/0101019fa594b4f3-0de09baf-7a03-49dc-ae70-cc2d3c980735-000000/"
    "4LPgzAGNX53rIJbdoCldOnDsW1V-id4nGUA0r3z-h6c=452"
)

STEP1_OK = {
    "ephemeral_key": "ek_live_testkey123",
    "invoice_id": "in_test_abc456",
    "merchant": {
        "business_name": "Anthropic, PBC",
        "support_email": "support@anthropic.com",
    },
}
STEP2_OK = {
    "number": "0KSERM3P-0007",
    "currency": "eur",
    "due_date": None,
    "status_transitions": {"finalized_at": 1781732763, "paid_at": 1781732766},
    "invoice_pdf": "https://pay.stripe.com/invoice/acct_x/live_y/pdf?s=il",
    "customer_name": "ZebraMed",
    "lines": {
        "data": [
            {
                "hosted_invoice_product_name": "Team plan - Standard",
                "hosted_invoice_short_description": "Team plan - Standard",
                "description": "5 x Team plan",
                "quantity": 5,
                "amount_excluding_tax": 10520,
                "tax_amounts": [],
                "period": {"start": 1780869165, "end": 1783461165},
            }
        ]
    },
}


def _resp(json_data=None, content=b"", status_code=200):
    response = Mock()
    response.status_code = status_code
    response.content = content
    response.json = Mock(return_value=json_data or {})
    response.raise_for_status = Mock()
    return response


class TestHostedInvoiceImportStripe(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, lang="en_US"))
        cls.company = cls.env.ref("base.main_company")
        cls.expense_account = cls.env["account.account"].create(
            {
                "code": "612STR",
                "name": "Stripe expense",
                "account_type": "expense",
                "company_ids": [Command.set([cls.company.id])],
            }
        )
        cls.income_account = cls.env["account.account"].create(
            {
                "code": "707STR",
                "name": "Stripe income",
                "account_type": "income",
                "company_ids": [Command.set([cls.company.id])],
            }
        )
        cls.product = (
            cls.env["product.product"]
            .with_company(cls.company.id)
            .create(
                {
                    "name": "Team plan",
                    "property_account_income_id": cls.income_account.id,
                    "property_account_expense_id": cls.expense_account.id,
                }
            )
        )
        cls.partner = cls.env["res.partner"].create(
            {"name": "Anthropic, PBC", "is_company": True}
        )
        cls.env["account.invoice.import.hosted.supplier.mapping"].create(
            {
                "company_id": cls.company.id,
                "provider": "stripe",
                "external_key": "Anthropic, PBC",
                "partner_id": cls.partner.id,
            }
        )
        cls.env["account.invoice.import.hosted.product.mapping"].create(
            {
                "company_id": cls.company.id,
                "provider": "stripe",
                "external_key": "Team plan - Standard",
                "product_id": cls.product.id,
            }
        )

    def _create_source(self, url):
        return self.env["account.invoice.import.hosted.source"].create(
            {"company_id": self.company.id, "source_url": url}
        )

    def test_happy_path_two_step_flow_and_email_unwrap(self):
        source = self._create_source(WRAPPED_URL)
        with patch(f"{MODULE}.requests.get") as mock_get:
            mock_get.side_effect = [
                _resp(json_data=STEP1_OK),
                _resp(json_data=STEP2_OK),
                _resp(content=b"invoice-pdf-bytes"),
                _resp(status_code=404),  # receipt lookup fails, non-blocking
            ]
            source.process_sources()

        self.assertEqual(source.state, "processed")
        self.assertEqual(source.provider, "stripe")
        self.assertEqual(source.partner_id, self.partner)
        self.assertEqual(source.invoice_number, "0KSERM3P-0007")
        self.assertTrue(source.move_id)
        line = source.move_id.invoice_line_ids
        self.assertEqual(len(line), 1)
        self.assertEqual(line.quantity, 5)
        # 10520 cents / 100 / 5 qty = 21.04
        self.assertAlmostEqual(line.price_unit, 21.04, places=2)
        self.assertIn("Period from", line.name)

        # Step2 call must carry the (never-logged) bearer ephemeral key.
        step2_kwargs = mock_get.call_args_list[1].kwargs
        self.assertEqual(
            step2_kwargs["headers"]["Authorization"], "Bearer ek_live_testkey123"
        )

        attachments = self.env["ir.attachment"].search(
            [
                ("res_model", "=", "account.move"),
                ("res_id", "=", source.move_id.id),
            ]
        )
        self.assertEqual(len(attachments), 1)  # only invoice pdf, receipt failed

    def test_expired_invoice(self):
        source = self._create_source(RAW_URL + "-expired")
        with patch(f"{MODULE}.requests.get") as mock_get:
            mock_get.return_value = _resp(json_data={"expired": True})
            source.process_sources()
        self.assertEqual(source.state, "expired")
        self.assertFalse(source.move_id)
        self.assertEqual(mock_get.call_count, 1)  # never attempts step 2

    def test_missing_ephemeral_key_is_error(self):
        source = self._create_source(RAW_URL + "-noeph")
        with patch(f"{MODULE}.requests.get") as mock_get:
            mock_get.return_value = _resp(json_data={"invoice_id": "in_x"})
            source.process_sources()
        self.assertEqual(source.state, "error")

    def test_product_description_fallback_chain(self):
        payload = dict(STEP2_OK)
        payload["lines"] = {
            "data": [
                {
                    "hosted_invoice_product_name": None,
                    "hosted_invoice_short_description": None,
                    "description": "Team plan - Standard",
                    "quantity": 1,
                    "amount_excluding_tax": 2104,
                    "tax_amounts": [],
                    "period": {},
                }
            ]
        }
        source = self._create_source(RAW_URL + "-fallback")
        with patch(f"{MODULE}.requests.get") as mock_get:
            mock_get.side_effect = [
                _resp(json_data=STEP1_OK),
                _resp(json_data=payload),
                _resp(content=b"invoice-pdf-bytes"),
                _resp(status_code=404),
            ]
            source.process_sources()
        self.assertEqual(source.state, "processed")

    def test_missing_supplier_mapping(self):
        payload = dict(STEP1_OK)
        payload["merchant"] = {
            "business_name": "Unknown Co",
            "support_email": "x@unknown.example",
        }
        source = self._create_source(RAW_URL + "-unknown-supplier")
        with patch(f"{MODULE}.requests.get") as mock_get:
            mock_get.return_value = _resp(json_data=payload)
            source.process_sources()
        self.assertEqual(source.state, "mapping_required")
        self.assertFalse(source.move_id)
