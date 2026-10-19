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
    "total_excluding_tax": 10520,
    "total": 10520,
    "lines": {
        "data": [
            {
                "hosted_invoice_product_name": "Team plan - Standard",
                "hosted_invoice_short_description": "Team plan - Standard",
                "description": "5 x Team plan",
                "quantity": 5,
                "amount": 10520,
                "taxes": [
                    {
                        "amount": 0,
                        "tax_behavior": "exclusive",
                        "taxability_reason": "reverse_charge",
                        "taxable_amount": 0,
                        "type": "tax_rate_details",
                    }
                ],
                "period": {"start": 1780869165, "end": 1783461165},
            }
        ]
    },
}


def _vat_tax(amount, behavior="exclusive", reason="standard_rated", taxable=None):
    return {
        "amount": amount,
        "tax_behavior": behavior,
        "taxability_reason": reason,
        "taxable_amount": taxable,
        "type": "tax_rate_details",
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
        cls.tax_reverse_charge = cls._create_vat_tax(
            "Reverse charge", 0.0, "account_tax_unece.tax_categ_ae"
        )
        cls.tax_standard = cls._create_vat_tax(
            "VAT 20%", 20.0, "account_tax_unece.tax_categ_s"
        )
        cls.product = (
            cls.env["product.product"]
            .with_company(cls.company.id)
            .create(
                {
                    "name": "Team plan",
                    "property_account_income_id": cls.income_account.id,
                    "property_account_expense_id": cls.expense_account.id,
                    "supplier_taxes_id": [Command.set(cls.tax_reverse_charge.ids)],
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

    @classmethod
    def _create_vat_tax(cls, name, amount, categ_xmlid):
        return cls.env["account.tax"].create(
            {
                "name": f"Hosted test {name}",
                "company_id": cls.company.id,
                "type_tax_use": "purchase",
                "amount_type": "percent",
                "amount": amount,
                "unece_type_id": cls.env.ref("account_tax_unece.tax_type_vat").id,
                "unece_categ_id": cls.env.ref(categ_xmlid).id,
            }
        )

    def _process(self, source, invoice_data):
        with patch(f"{MODULE}.requests.get") as mock_get:
            mock_get.side_effect = [
                _resp(json_data=STEP1_OK),
                _resp(json_data=invoice_data),
                _resp(content=b"invoice-pdf-bytes"),
                _resp(status_code=404),
            ]
            source.process_sources()
        self.assertEqual(source.state, "processed", source.error_message)
        return source.move_id

    def _invoice_data(self, line_vals, total_excluding_tax, total):
        invoice_data = dict(STEP2_OK)
        line = dict(STEP2_OK["lines"]["data"][0], **line_vals)
        invoice_data.update(
            {
                "lines": {"data": [line]},
                "total_excluding_tax": total_excluding_tax,
                "total": total,
            }
        )
        return invoice_data

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
        self.assertEqual(line.product_id, self.product)
        self.assertEqual(line.quantity, 5)
        # 10520 cents / 100 / 5 qty = 21.04
        self.assertAlmostEqual(line.price_unit, 21.04, places=2)
        self.assertEqual(line.tax_ids, self.tax_reverse_charge)
        self.assertIn("Period from", line.name)
        self.assertAlmostEqual(source.move_id.amount_total, 105.20, places=2)

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

    def test_product_without_vendor_taxes_uses_vat_category(self):
        self.product.supplier_taxes_id = False
        move = self._process(self._create_source(RAW_URL + "-notax"), STEP2_OK)
        # Stripe flags the line as reverse charge: VAT category AE
        self.assertEqual(move.invoice_line_ids.tax_ids, self.tax_reverse_charge)

    def test_standard_vat_from_product(self):
        self.product.supplier_taxes_id = self.tax_standard
        invoice_data = self._invoice_data(
            {"quantity": 1, "amount": 1000, "taxes": [_vat_tax(200, taxable=1000)]},
            total_excluding_tax=1000,
            total=1200,
        )
        move = self._process(self._create_source(RAW_URL + "-vat"), invoice_data)
        self.assertEqual(move.invoice_line_ids.tax_ids, self.tax_standard)
        self.assertAlmostEqual(move.amount_untaxed, 10.0, places=2)
        self.assertAlmostEqual(move.amount_total, 12.0, places=2)

    def test_global_discount_adds_adjustment_line(self):
        self.company.adjustment_credit_account_id = self.expense_account
        self.company.adjustment_debit_account_id = self.expense_account
        # 10% discount on the invoice: only visible in the invoice totals
        invoice_data = self._invoice_data({}, total_excluding_tax=9468, total=9468)
        move = self._process(self._create_source(RAW_URL + "-disc"), invoice_data)
        self.assertEqual(len(move.invoice_line_ids), 2)
        self.assertAlmostEqual(move.amount_untaxed, 94.68, places=2)

    def test_supplier_shrink_setting_is_ignored(self):
        self.partner.with_company(self.company).invoice_import_shrink_lines = "single"
        move = self._process(self._create_source(RAW_URL + "-shrink"), STEP2_OK)
        self.assertEqual(move.invoice_line_ids.product_id, self.product)

    def test_build_lines_taxes(self):
        source_model = self.env["account.invoice.import.hosted.source"]
        base_line = STEP2_OK["lines"]["data"][0]
        lines = source_model._stripe_build_lines(
            {
                "lines": {
                    "data": [
                        dict(
                            base_line,
                            amount=1200,
                            taxes=[_vat_tax(200, "inclusive", taxable=1000)],
                        ),
                        dict(
                            base_line,
                            amount=1000,
                            taxes=[_vat_tax(55, "exclusive", "reduced_rated", 1000)],
                        ),
                        dict(
                            base_line,
                            amount=1000,
                            taxes=[
                                _vat_tax(50, taxable=1000),
                                _vat_tax(30, taxable=1000),
                            ],
                        ),
                        dict(
                            base_line,
                            amount=1000,
                            taxes=[_vat_tax(0, reason="not_collecting", taxable=0)],
                        ),
                    ]
                }
            }
        )
        inclusive, reduced, two_taxes, not_collecting = lines
        self.assertAlmostEqual(inclusive["amount"], 10.0)
        self.assertAlmostEqual(inclusive["tax_amount"], 2.0)
        self.assertEqual(
            (inclusive["vat_category"], inclusive["vat_rate"]), ("S", 20.0)
        )
        self.assertAlmostEqual(reduced["amount"], 10.0)
        self.assertEqual((reduced["vat_category"], reduced["vat_rate"]), ("S", 5.5))
        self.assertAlmostEqual(two_taxes["tax_amount"], 0.8)
        self.assertFalse(two_taxes["vat_category"])
        self.assertFalse(not_collecting["vat_category"])

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
                    "amount": 2104,
                    "taxes": [],
                    "period": {},
                }
            ]
        }
        payload.update({"total_excluding_tax": 2104, "total": 2104})
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
