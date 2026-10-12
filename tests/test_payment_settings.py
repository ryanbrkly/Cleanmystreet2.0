"""Yoco keys entered in Admin → Site settings."""
import base64
import hashlib
import hmac
import json
import time
import unittest
from unittest import mock

from cms.db import query
from test_flows import Base

LIVE = "sk_live_" + "a1b2c3d4" * 4
HOOK = "whsec_" + base64.b64encode(b"another-signing-secret-32-bytes!").decode()


def resp(status=200, data=None):
    r = mock.Mock(status_code=status, content=b"x", text="x")
    r.json.return_value = data or {}
    return r


class PaymentSettingsTests(Base):
    def save(self, token, **kw):
        data = {"_csrf": token, "action": "payments_save", "confirm_password": "pw-123456"}
        data.update(kw)
        return self.client.post("/admin/settings", data=data, follow_redirects=True).get_data(as_text=True)

    def test_save_encrypted_masked_and_used_for_checkout(self):
        self.app.config.update(YOCO_SECRET_KEY="", YOCO_WEBHOOK_SECRET="")
        token = self.login()
        page = self.client.get("/admin/settings").get_data(as_text=True)
        self.assertIn("Payments (Yoco)", page)
        self.assertIn("Demo, no money moves", page)
        # wrong password / wrong key types are refused
        self.assertIn("Enter your admin password", self.save(token, confirm_password="nope", yoco_secret_key=LIVE))
        self.assertIn("Don’t use the public key", self.save(token, yoco_secret_key="pk_live_123"))
        self.assertIn("should start with whsec_", self.save(token, yoco_webhook_secret="abc"))
        self.assertIn("Add a Yoco secret key before", self.save(token, payment_mode="yoco"))
        # save live key and switch on Yoco
        html = self.save(token, yoco_secret_key=LIVE, yoco_webhook_secret=HOOK, payment_mode="yoco")
        self.assertIn("LIVE: real money", html)
        self.assertNotIn(LIVE, html)
        self.assertIn("sk_live_…" + LIVE[-4:], html)
        with self.app.app_context():
            stored = query("SELECT value FROM settings WHERE key='yoco_secret_key'", one=True)["value"]
        self.assertTrue(stored.startswith("enc1:"))
        self.assertNotIn(LIVE, stored)
        # public pages: no demo banner, demo payment page gone
        self.assertNotIn("Demo mode", self.client.get("/").get_data(as_text=True))
        # a contribution now goes to Yoco with the saved key
        self.submit_request()
        p, token = self.approve(goal="1000")
        with mock.patch("cms.payments.requests.request",
                        return_value=resp(200, {"id": "ch_1", "redirectUrl": "https://c.yoco.com/ch_1"})) as m:
            r = self.contribute(p["slug"], "200", token)
        self.assertEqual(r.headers["Location"], "https://c.yoco.com/ch_1")
        self.assertEqual(m.call_args.kwargs["headers"]["Authorization"], f"Bearer {LIVE}")
        with self.app.app_context():
            tok = query("SELECT return_token FROM contributions", one=True)["return_token"]
        self.assertEqual(self.client.get(f"/pay/demo/{tok}").status_code, 404)
        # the saved webhook secret verifies Yoco's webhooks
        body = json.dumps({"type": "payment.succeeded", "payload": {"id": "p_1", "metadata": {"checkoutId": "ch_1"}}}).encode()
        ts, mid = str(int(time.time())), "msg_1"
        key = base64.b64decode(HOOK.split("_", 1)[1])
        sig = base64.b64encode(hmac.new(key, f"{mid}.{ts}.".encode() + body, hashlib.sha256).digest()).decode()
        r = self.client.post("/webhooks/yoco", data=body, content_type="application/json",
                             headers={"webhook-id": mid, "webhook-timestamp": ts, "webhook-signature": f"v1,{sig}"})
        self.assertEqual(r.status_code, 200)
        with self.app.app_context():
            self.assertEqual(query("SELECT status FROM contributions", one=True)["status"], "paid")

    def test_check_keys_webhook_setup_and_forget(self):
        token = self.login()
        self.save(token, yoco_secret_key="sk_test_" + "x" * 30)
        with mock.patch("cms.payments.requests.get", return_value=resp(200, [])):
            r = self.client.post("/admin/settings", data={"_csrf": token, "action": "payments_check"}, follow_redirects=True)
        self.assertIn("Yoco accepted the test secret key", r.get_data(as_text=True))
        with mock.patch("cms.payments.requests.get", return_value=resp(401)):
            r = self.client.post("/admin/settings", data={"_csrf": token, "action": "payments_check"}, follow_redirects=True)
        self.assertIn("Yoco rejected the secret key", r.get_data(as_text=True))
        with mock.patch("cms.payments.requests.request", return_value=resp(200, {"id": "wh_1", "secret": HOOK})) as m:
            r = self.client.post("/admin/settings", data={"_csrf": token, "action": "payments_webhook",
                                                          "confirm_password": "pw-123456"}, follow_redirects=True)
        self.assertIn("Webhook set up with Yoco", r.get_data(as_text=True))
        self.assertTrue(m.call_args.kwargs["json"]["url"].endswith("/webhooks/yoco"))
        r = self.client.post("/admin/settings", data={"_csrf": token, "action": "payments_forget",
                                                      "confirm_password": "pw-123456"}, follow_redirects=True)
        self.assertIn("Saved Yoco keys removed", r.get_data(as_text=True))
        with self.app.app_context():
            self.assertEqual(query("SELECT value FROM settings WHERE key='yoco_secret_key'", one=True)["value"], "")

    def test_changed_secret_key_makes_saved_keys_unreadable(self):
        token = self.login()
        self.save(token, yoco_secret_key=LIVE, payment_mode="yoco")
        self.app.config["SECRET_KEY"] = "a-different-secret"
        token = self.login()
        page = self.client.get("/admin/settings").get_data(as_text=True)
        self.assertIn("Saved keys can’t be read", page)

    def test_needs_login_and_csrf(self):
        r = self.app.test_client().post("/admin/settings", data={"action": "payments_check"})
        self.assertIn(r.status_code, (302, 400))
        self.login()
        self.assertEqual(self.client.post("/admin/settings", data={"action": "payments_save"}).status_code, 400)
