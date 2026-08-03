# Copyright 2026 Akretion France (http://www.akretion.com/)
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from unittest.mock import Mock, patch

from odoo import Command
from odoo.tests.common import TransactionCase

MODULE = (
    "odoo.addons.account_invoice_import_hosted_invoice_withorb.models"
    ".account_invoice_import_hosted_source"
)

INVOICE_URL = "https://invoices.withorb.com/view?token=IlJLaVk3TTl0V2dGRDJBTloi"


def _sample_payload(with_zero_line=True):
    line_items = [
        {
            "name": "Shared - Cluster usage",
            "quantity": 1.0,
            "subtotal": "25.00",
            "start_date": "2026-06-08T00:00:00+00:00",
            "end_date": "2026-07-08T00:00:00+00:00",
            "tax_amounts": [{"tax_amount": "0.00"}],
        },
    ]
    if with_zero_line:
        line_items.append(
            {
                "name": "Shared - vCPU Overages",
                "quantity": 0.389902,
                "subtotal": "0.00007798039999999986",
                "start_date": "2026-06-08T00:00:00+00:00",
                "end_date": "2026-07-08T00:00:00+00:00",
                "tax_amounts": [{"tax_amount": "0.00"}],
            }
        )
    return {
        "account_name": "Tinybird",
        "invoice": {
            "account_settings": {
                "company_name": "Tinybird Inc. ",
                "invoice_delivery": {"reply_to": "billing@tinybird.co"},
            },
            "invoice_number": "ZLOGHM-00003",
            "invoice_date": "2026-07-08T00:00:00+00:00",
            "due_date": "2026-07-09T00:00:00+00:00",
            "invoicing_pricing_unit": {"short_name": "USD"},
            "invoice_pdf": "https://assets.withorb.com/invoice/x?token=y",
            "receipt_pdf": "https://assets.withorb.com/receipt/x?token=y",
            "line_items": line_items,
        },
    }


class TestHostedInvoiceImportWithorb(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, lang="en_US"))
        cls.company = cls.env.ref("base.main_company")
        cls.expense_account = cls.env["account.account"].create(
            {
                "code": "612WOB",
                "name": "Withorb expense",
                "account_type": "expense",
                "company_ids": [Command.set([cls.company.id])],
            }
        )
        cls.income_account = cls.env["account.account"].create(
            {
                "code": "707WOB",
                "name": "Withorb income",
                "account_type": "income",
                "company_ids": [Command.set([cls.company.id])],
            }
        )
        cls.product = (
            cls.env["product.product"]
            .with_company(cls.company.id)
            .create(
                {
                    "name": "Cluster usage",
                    "property_account_income_id": cls.income_account.id,
                    "property_account_expense_id": cls.expense_account.id,
                }
            )
        )
        cls.partner = cls.env["res.partner"].create(
            {"name": "Tinybird Inc.", "is_company": True}
        )
        cls.env["account.invoice.import.hosted.supplier.mapping"].create(
            {
                "company_id": cls.company.id,
                "provider": "withorb",
                "external_key": "Tinybird Inc.",
                "partner_id": cls.partner.id,
            }
        )
        cls.env["account.invoice.import.hosted.product.mapping"].create(
            {
                "company_id": cls.company.id,
                "provider": "withorb",
                "external_key": "Shared - Cluster usage",
                "product_id": cls.product.id,
            }
        )

    def _mock_response(self, json_data=None, content=b"", status_code=200):
        response = Mock()
        response.status_code = status_code
        response.content = content
        response.json = Mock(return_value=json_data or {})

        def _raise():
            if status_code >= 400:
                raise Exception(f"HTTP {status_code}")

        response.raise_for_status = Mock(side_effect=_raise)
        return response

    def _create_source(self, url=INVOICE_URL):
        return self.env["account.invoice.import.hosted.source"].create(
            {"company_id": self.company.id, "source_url": url}
        )

    def test_happy_path_and_user_agent(self):
        source = self._create_source()
        with patch(f"{MODULE}.requests.get") as mock_get:
            mock_get.side_effect = [
                self._mock_response(json_data=_sample_payload()),
                self._mock_response(content=b"invoice-pdf-bytes"),
                self._mock_response(content=b"receipt-pdf-bytes"),
            ]
            source.process_sources()

        self.assertEqual(source.state, "processed")
        self.assertEqual(source.partner_id, self.partner)
        self.assertEqual(source.invoice_number, "ZLOGHM-00003")
        self.assertTrue(source.move_id)
        self.assertEqual(len(source.move_id.invoice_line_ids), 1)  # zero line skipped
        self.assertIn("Period from", source.move_id.invoice_line_ids.name)

        # First call (the invoice_from_link fetch) must use a browser UA.
        first_call_kwargs = mock_get.call_args_list[0].kwargs
        self.assertIn("Mozilla", first_call_kwargs["headers"]["User-Agent"])

        attachments = self.env["ir.attachment"].search(
            [
                ("res_model", "=", "account.move"),
                ("res_id", "=", source.move_id.id),
            ]
        )
        self.assertEqual(len(attachments), 2)
        # Filenames use the supplier abbreviation (name fallback, spaces -> _),
        # the ISO invoice date and the invoice number.
        invoice_att = attachments.filtered(
            lambda a: not a.name.endswith("-receipt.pdf")
        )
        receipt_att = attachments - invoice_att
        self.assertEqual(
            invoice_att.name, "2026-07-08-Tinybird_Inc.-ZLOGHM-00003.pdf"
        )
        self.assertEqual(
            receipt_att.name, "2026-07-08-Tinybird_Inc.-ZLOGHM-00003-receipt.pdf"
        )
        # The invoice PDF must be stored before the receipt (smaller id).
        self.assertLess(invoice_att.id, receipt_att.id)

    def test_supplier_ref_used_in_filename(self):
        self.partner.ref = "TINY BIRD"
        source = self._create_source(url=INVOICE_URL + "-ref")
        with patch(f"{MODULE}.requests.get") as mock_get:
            mock_get.side_effect = [
                self._mock_response(json_data=_sample_payload()),
                self._mock_response(content=b"invoice-pdf-bytes"),
                self._mock_response(content=b"receipt-pdf-bytes"),
            ]
            source.process_sources()

        self.assertEqual(source.state, "processed")
        attachments = self.env["ir.attachment"].search(
            [
                ("res_model", "=", "account.move"),
                ("res_id", "=", source.move_id.id),
            ]
        )
        invoice_att = attachments.filtered(
            lambda a: not a.name.endswith("-receipt.pdf")
        )
        self.assertEqual(
            invoice_att.name, "2026-07-08-TINY_BIRD-ZLOGHM-00003.pdf"
        )

    def test_missing_supplier_mapping(self):
        payload = _sample_payload()
        payload["invoice"]["account_settings"]["company_name"] = "Unknown Corp"
        payload["account_name"] = "Unknown"
        payload["invoice"]["account_settings"]["invoice_delivery"][
            "reply_to"
        ] = "unknown@example.com"
        source = self._create_source(url=INVOICE_URL + "-unknown")
        with patch(f"{MODULE}.requests.get") as mock_get:
            mock_get.return_value = self._mock_response(json_data=payload)
            source.process_sources()
        self.assertEqual(source.state, "mapping_required")
        self.assertFalse(source.move_id)

    def test_missing_product_mapping(self):
        payload = _sample_payload(with_zero_line=False)
        payload["invoice"]["line_items"][0]["name"] = "Unmapped Product"
        source = self._create_source(url=INVOICE_URL + "-unmapped-product")
        with patch(f"{MODULE}.requests.get") as mock_get:
            mock_get.return_value = self._mock_response(json_data=payload)
            source.process_sources()
        self.assertEqual(source.state, "mapping_required")
        self.assertIn("Unmapped Product", source.missing_mapping_info)

    def test_duplicate_by_invoice_number_and_partner(self):
        source1 = self._create_source(url=INVOICE_URL + "-dup-1")
        with patch(f"{MODULE}.requests.get") as mock_get:
            mock_get.side_effect = [
                self._mock_response(json_data=_sample_payload()),
                self._mock_response(content=b"invoice-pdf-bytes"),
                self._mock_response(content=b"receipt-pdf-bytes"),
            ]
            source1.process_sources()
        self.assertEqual(source1.state, "processed")

        source2 = self._create_source(url=INVOICE_URL + "-dup-2")
        with patch(f"{MODULE}.requests.get") as mock_get:
            mock_get.return_value = self._mock_response(json_data=_sample_payload())
            source2.process_sources()
        self.assertEqual(source2.state, "duplicate")
        self.assertEqual(source2.move_id, source1.move_id)

    def test_expired_link(self):
        source = self._create_source(url=INVOICE_URL + "-expired")
        with patch(f"{MODULE}.requests.get") as mock_get:
            mock_get.return_value = self._mock_response(status_code=404)
            source.process_sources()
        self.assertEqual(source.state, "expired")
        self.assertFalse(source.move_id)
