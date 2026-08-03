# Copyright 2026 Akretion France (http://www.akretion.com/)
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from unittest.mock import Mock, patch

from odoo.tests.common import TransactionCase

from odoo.addons.account_invoice_import_hosted_invoice_stripe.models.account_invoice_import_hosted_source import (  # noqa: E501
    StripePortalExpired,
)

MODULE = (
    "odoo.addons.account_invoice_import_hosted_invoice_stripe.models"
    ".account_invoice_import_hosted_source"
)

PORTAL_URL = "https://billing.stripe.com/p/session/live_portaltoken123"
PORTAL_HTML = (
    '<html><head></head><body><script>window.__bootstrap = '
    '{"portal_session_id":"bps_test_123","session_api_key":'
    '"ek_live_sessionkey_456","other":"x"};</script></body></html>'
)
# HTML with backslash-escaped quotes (as embedded in some JSON blobs).
PORTAL_HTML_ESCAPED = (
    '<script>var s = "{\\"portal_session_id\\":\\"bps_test_123\\",'
    '\\"session_api_key\\":\\"ek_live_sessionkey_456\\"}";</script>'
)


def _resp(json_data=None, text="", status_code=200):
    response = Mock()
    response.status_code = status_code
    response.text = text
    response.json = Mock(return_value=json_data or {})
    response.raise_for_status = Mock()
    return response


class TestStripePortalLister(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.model = cls.env["account.invoice.import.hosted.source"]

    def test_extract_session_plain(self):
        sid, key = self.model._stripe_portal_extract_session(PORTAL_HTML)
        self.assertEqual(sid, "bps_test_123")
        self.assertEqual(key, "ek_live_sessionkey_456")

    def test_extract_session_escaped(self):
        sid, key = self.model._stripe_portal_extract_session(PORTAL_HTML_ESCAPED)
        self.assertEqual(sid, "bps_test_123")
        self.assertEqual(key, "ek_live_sessionkey_456")

    def test_extract_session_missing(self):
        sid, key = self.model._stripe_portal_extract_session("<html></html>")
        self.assertFalse(sid)
        self.assertFalse(key)

    def test_list_invoice_urls_pagination(self):
        page1 = {
            "object": "list",
            "has_more": True,
            "data": [
                {
                    "id": "in_1",
                    "object": "invoice",
                    "status": "paid",
                    "hosted_invoice_url": "https://invoice.stripe.com/i/a/1?s=il",
                    "effective_at": 1784662576,
                },
            ],
        }
        page2 = {
            "object": "list",
            "has_more": False,
            "data": [
                {
                    "id": "in_2",
                    "object": "invoice",
                    "status": "paid",
                    "hosted_invoice_url": "https://invoice.stripe.com/i/a/2?s=il",
                    "effective_at": 1784662000,
                },
            ],
        }
        with patch(f"{MODULE}.requests.get") as mock_get:
            mock_get.side_effect = [
                _resp(text=PORTAL_HTML),  # portal page
                _resp(json_data=page1),  # invoices page 1
                _resp(json_data=page2),  # invoices page 2
            ]
            result = self.model._stripe_portal_list_invoice_urls(PORTAL_URL)

        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["stripe_id"], "in_1")
        self.assertEqual(result[1]["stripe_id"], "in_2")
        # The second invoices call must carry the auth header + starting_after.
        page2_call = mock_get.call_args_list[2]
        self.assertEqual(
            page2_call.kwargs["headers"]["Authorization"],
            "Bearer ek_live_sessionkey_456",
        )
        self.assertIn(("starting_after", "in_1"), page2_call.kwargs["params"])

    def test_list_invoice_urls_date_filter(self):
        page = {
            "object": "list",
            "has_more": False,
            "data": [
                {
                    "id": "in_recent",
                    "object": "invoice",
                    "status": "paid",
                    "hosted_invoice_url": "https://invoice.stripe.com/i/a/r?s=il",
                    "effective_at": 1784662576,  # 2026
                },
                {
                    "id": "in_old",
                    "object": "invoice",
                    "status": "paid",
                    "hosted_invoice_url": "https://invoice.stripe.com/i/a/o?s=il",
                    "effective_at": 1577836800,  # 2020-01-01
                },
            ],
        }
        with patch(f"{MODULE}.requests.get") as mock_get:
            mock_get.side_effect = [
                _resp(text=PORTAL_HTML),
                _resp(json_data=page),
            ]
            result = self.model._stripe_portal_list_invoice_urls(
                PORTAL_URL, since_date="2026-01-01"
            )
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["stripe_id"], "in_recent")

    def test_expired_portal_session(self):
        with patch(f"{MODULE}.requests.get") as mock_get:
            mock_get.side_effect = [_resp(text="<html>expired</html>")]
            with self.assertRaises(StripePortalExpired):
                self.model._stripe_portal_list_invoice_urls(PORTAL_URL)

    def test_expired_portal_http(self):
        with patch(f"{MODULE}.requests.get") as mock_get:
            mock_get.side_effect = [_resp(status_code=410)]
            with self.assertRaises(StripePortalExpired):
                self.model._stripe_portal_list_invoice_urls(PORTAL_URL)
