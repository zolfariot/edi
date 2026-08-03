# Copyright 2026 Akretion France (http://www.akretion.com/)
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from unittest.mock import Mock, patch

from odoo import Command
from odoo.tests.common import TransactionCase

PORTAL_URL = "https://billing.stripe.com/p/session/live_portaltoken123"
PORTAL_HTML = (
    '<script>window.__b = {"portal_session_id":"bps_test_123",'
    '"session_api_key":"ek_live_sessionkey_456"};</script>'
)
INVOICE_URL = "https://invoice.stripe.com/i/acct_x/live_secret1?s=il"

INVOICES_PAGE = {
    "object": "list",
    "has_more": False,
    "data": [
        {
            "id": "in_1",
            "object": "invoice",
            "status": "paid",
            "hosted_invoice_url": INVOICE_URL,
            "effective_at": 1784662576,
        }
    ],
}
STEP1_OK = {
    "ephemeral_key": "ek_live_testkey123",
    "invoice_id": "in_test_abc456",
    "merchant": {
        "business_name": "DeepInfra Inc",
        "support_email": "support@deepinfra.com",
    },
}
STEP2_OK = {
    "number": "DI-0007",
    "currency": "usd",
    "due_date": None,
    "status_transitions": {"finalized_at": 1784662576, "paid_at": 1784662576},
    "invoice_pdf": None,
    "customer_name": "My Company",
    "lines": {
        "data": [
            {
                "hosted_invoice_product_name": "DeepInfra Test Credits",
                "description": "DeepInfra Test Credits",
                "quantity": 1,
                "amount_excluding_tax": 1000,
                "tax_amounts": [],
                "period": {},
            }
        ]
    },
}


def _resp(json_data=None, text="", content=b"", status_code=200):
    response = Mock()
    response.status_code = status_code
    response.text = text
    response.content = content
    response.json = Mock(return_value=json_data or {})
    response.raise_for_status = Mock()
    return response


class TestDownloadDeepInfra(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, lang="en_US"))
        cls.company = cls.env.ref("base.main_company")
        cls.expense_account = cls.env["account.account"].create(
            {
                "code": "612DITEST",
                "name": "DeepInfra expense",
                "account_type": "expense",
                "company_ids": [Command.set([cls.company.id])],
            }
        )
        cls.income_account = cls.env["account.account"].create(
            {
                "code": "707DITEST",
                "name": "DeepInfra income",
                "account_type": "income",
                "company_ids": [Command.set([cls.company.id])],
            }
        )
        cls.product = (
            cls.env["product.product"]
            .with_company(cls.company.id)
            .create(
                {
                    "name": "AI credits",
                    "property_account_income_id": cls.income_account.id,
                    "property_account_expense_id": cls.expense_account.id,
                }
            )
        )
        cls.partner = cls.env["res.partner"].create(
            {"name": "DeepInfra Inc", "is_company": True}
        )
        cls.env["account.invoice.import.hosted.product.mapping"].create(
            {
                "company_id": cls.company.id,
                "provider": "stripe",
                "external_key": "DeepInfra Test Credits",
                "product_id": cls.product.id,
            }
        )
        cls.config = cls.env["account.invoice.download.config"].create(
            {
                "company_id": cls.company.id,
                "partner_id": cls.partner.id,
                "backend": "deepinfra",
                "deepinfra_api_key": "sk_deepinfra_test",
            }
        )

    def _full_side_effect(self):
        # Single ordered response stream: every module in the flow shares the
        # same ``requests`` object, so one patch intercepts all HTTP calls.
        return [
            _resp(json_data={"url": PORTAL_URL}),  # DeepInfra billing portal
            _resp(text=PORTAL_HTML),  # Stripe portal page
            _resp(json_data=INVOICES_PAGE),  # Stripe portal invoice list
            _resp(json_data=STEP1_OK),  # hosted page step 1
            _resp(json_data=STEP2_OK),  # invoice data step 2
            _resp(json_data={}),  # receipt lookup -> no file_url
        ]

    def test_run_creates_bill_via_hosted_source(self):
        credentials = self.config.prepare_credentials()
        with patch("requests.get") as mock_get:
            mock_get.side_effect = self._full_side_effect()
            move_ids, log_id = self.config.run(credentials)

        self.assertEqual(len(move_ids), 1)
        source = self.env["account.invoice.import.hosted.source"].search(
            [("download_config_id", "=", self.config.id)]
        )
        self.assertEqual(len(source), 1)
        self.assertEqual(source.state, "processed")
        self.assertEqual(source.forced_partner_id, self.partner)
        self.assertEqual(source.move_id.id, move_ids[0])
        move = source.move_id
        self.assertEqual(move.move_type, "in_invoice")
        self.assertEqual(move.commercial_partner_id, self.partner)
        self.assertEqual(len(move.invoice_line_ids), 1)
        self.assertEqual(move.invoice_line_ids.product_id, self.product)
        # DeepInfra API key never leaks the portal token into storage.
        self.assertNotIn(PORTAL_URL, source.raw_payload or "")

    def test_rerun_is_idempotent(self):
        credentials = self.config.prepare_credentials()
        with patch("requests.get") as mock_get:
            mock_get.side_effect = self._full_side_effect()
            self.config.run(credentials)

        with patch("requests.get") as mock_get:
            # Only billing portal + portal page + invoices list are hit; the
            # already-processed source is skipped before any per-invoice fetch.
            mock_get.side_effect = [
                _resp(json_data={"url": PORTAL_URL}),
                _resp(text=PORTAL_HTML),
                _resp(json_data=INVOICES_PAGE),
            ]
            move_ids, log_id = self.config.run(credentials)

        self.assertEqual(move_ids, [])
        sources = self.env["account.invoice.import.hosted.source"].search(
            [("download_config_id", "=", self.config.id)]
        )
        self.assertEqual(len(sources), 1)
        moves = self.env["account.move"].search(
            [
                ("commercial_partner_id", "=", self.partner.id),
                ("move_type", "=", "in_invoice"),
            ]
        )
        self.assertEqual(len(moves), 1)

    def test_missing_api_key_raises(self):
        self.config.deepinfra_api_key = False
        with self.assertRaises(Exception):
            self.config.credentials_stored()
