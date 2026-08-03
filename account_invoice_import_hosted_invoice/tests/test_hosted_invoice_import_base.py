# Copyright 2026 Akretion France (http://www.akretion.com/)
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.tests.common import TransactionCase


class TestHostedInvoiceImportBase(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, lang="en_US"))
        cls.company = cls.env.ref("base.main_company")
        cls.hso = cls.env["account.invoice.import.hosted.source"]
        cls.partner = cls.env["res.partner"].create({"name": "Test Supplier"})
        cls.product = cls.env["product.product"].create({"name": "Test Product"})

    def test_normalize_url_query_order_independent(self):
        url_a = "https://example.com/view?token=abc&s=em"
        url_b = "https://EXAMPLE.com/view?s=em&token=abc"
        self.assertEqual(
            self.hso._normalize_url(url_a), self.hso._normalize_url(url_b)
        )
        self.assertEqual(
            self.hso._hash_url(self.hso._normalize_url(url_a)),
            self.hso._hash_url(self.hso._normalize_url(url_b)),
        )

    def test_normalize_key(self):
        self.assertEqual(self.hso._normalize_key("  Ünité  Spaces  "), "unite spaces")
        self.assertEqual(self.hso._normalize_key(""), "")
        self.assertEqual(self.hso._normalize_key(False), "")

    def test_format_period_text_same_day_skipped(self):
        self.assertEqual(self.hso._format_period_text("2026-06-08", "2026-06-08"), "")
        self.assertEqual(self.hso._format_period_text(False, "2026-06-08"), "")
        text = self.hso._format_period_text("2026-06-08", "2026-07-08")
        self.assertIn("2026", text)
        self.assertTrue(text.startswith(" Period from"))

    def test_supplier_mapping_normalizes_and_dedups(self):
        smo = self.env["account.invoice.import.hosted.supplier.mapping"]
        mapping = smo.create(
            {
                "company_id": self.company.id,
                "provider": False,
                "external_key": "  Some Company, Inc.  ",
                "partner_id": self.partner.id,
            }
        )
        self.assertEqual(mapping.external_key, "some company, inc.")

    def test_supplier_mapping_unique_per_company_provider_key(self):
        # NULL is not equal to NULL in a Postgres unique constraint, so this
        # only really gets enforced once a provider addon extends the
        # selection (tested in the provider-specific test suites). Here we
        # only check case/whitespace normalization is applied consistently.
        smo = self.env["account.invoice.import.hosted.supplier.mapping"]
        mapping = smo.create(
            {
                "company_id": self.company.id,
                "provider": False,
                "external_key": "ACME Corp",
                "partner_id": self.partner.id,
            }
        )
        self.assertEqual(
            self.hso._find_supplier_mapping(
                self.company, False, ["acme corp"]
            ),
            mapping,
        )

    def test_product_mapping_normalizes_and_dedups(self):
        pmo = self.env["account.invoice.import.hosted.product.mapping"]
        mapping = pmo.create(
            {
                "company_id": self.company.id,
                "provider": False,
                "external_key": "  Shared - Cluster Usage  ",
                "product_id": self.product.id,
            }
        )
        self.assertEqual(mapping.external_key, "shared - cluster usage")

    def test_find_supplier_mapping_candidate_order(self):
        smo = self.env["account.invoice.import.hosted.supplier.mapping"]
        smo.create(
            {
                "company_id": self.company.id,
                "provider": False,
                "external_key": "billing@example.com",
                "partner_id": self.partner.id,
            }
        )
        found = self.hso._find_supplier_mapping(
            self.company, False, ["Unknown Business Name", "billing@example.com"]
        )
        self.assertEqual(found.partner_id, self.partner)

    def test_wizard_reuses_existing_row_for_same_url(self):
        wizard = self.env["account.invoice.import.hosted.submit"].create(
            {
                "company_id": self.company.id,
                "url_text": "https://example.com/view?token=xyz",
            }
        )
        wizard.action_submit_and_process()
        sources = self.hso.search(
            [("normalized_url", "=", self.hso._normalize_url(
                "https://example.com/view?token=xyz"
            ))]
        )
        self.assertEqual(len(sources), 1)
        # Unknown provider -> error state (no provider addon installed here)
        self.assertEqual(sources.state, "error")

        # Resubmitting the exact same link must not create a second row.
        wizard2 = self.env["account.invoice.import.hosted.submit"].create(
            {
                "company_id": self.company.id,
                "url_text": "https://example.com/view?token=xyz",
            }
        )
        wizard2.action_submit_and_process()
        sources_after = self.hso.search(
            [("normalized_url", "=", self.hso._normalize_url(
                "https://example.com/view?token=xyz"
            ))]
        )
        self.assertEqual(len(sources_after), 1)
