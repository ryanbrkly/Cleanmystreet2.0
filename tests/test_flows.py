"""End-to-end tests for the main flows. Run: python -m unittest discover tests -v"""
import base64
import hashlib
import hmac
import io
import json
import os
import re
import tempfile
import time
import unittest
from unittest import mock

from PIL import Image

from cms import create_app
from cms.db import execute, init_db, query
from cms.utils import youtube_id, parse_rand, rand

WEBHOOK_SECRET = "whsec_" + base64.b64encode(b"super-secret-signing-key-32bytes!").decode()


def png_bytes():
    buf = io.BytesIO()
    Image.new("RGB", (40, 30), (120, 180, 40)).save(buf, "PNG")
    buf.seek(0)
    return buf


class Base(unittest.TestCase):
    payment_mode = "demo"

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.app = create_app({
            "TESTING": True,
            "DATABASE": os.path.join(self.tmp, "t.sqlite3"),
            "UPLOAD_FOLDER": os.path.join(self.tmp, "uploads"),
            "PAYMENT_MODE": self.payment_mode,
            "YOCO_SECRET_KEY": "sk_test_dummy",
            "YOCO_WEBHOOK_SECRET": WEBHOOK_SECRET,
            "CRON_SECRET": "cron123",
        })
        self.client = self.app.test_client()
        with self.app.app_context():
            init_db()
            from werkzeug.security import generate_password_hash
            execute("INSERT INTO admins(email, name, password_hash) VALUES (?,?,?)",
                    ("a@x.co.za", "Admin Person", generate_password_hash("pw-123456")))

    # helpers
    def csrf(self, path="/request"):
        html = self.client.get(path).get_data(as_text=True)
        return re.search(r'name="_csrf" value="([^"]+)"', html).group(1)

    def login(self):
        token = self.csrf("/admin/login")
        r = self.client.post("/admin/login", data={"_csrf": token, "email": "a@x.co.za", "password": "pw-123456"})
        self.assertEqual(r.status_code, 302)
        # the session (and its CSRF token) is rotated on sign-in
        html = self.client.get("/admin/settings").get_data(as_text=True)
        return re.search(r'name="_csrf" value="([^"]+)"', html).group(1)

    def submit_request(self, **over):
        token = self.csrf()
        data = {"_csrf": token, "street_name": "Oak Avenue", "suburb": "Parkview", "city": "Johannesburg",
                "postcode": "2193", "description": "Overgrown verges and blocked drains.", "service_pref": "once",
                "funding_days": "21", "requester_name": "Ryan Test", "email": "ryan@example.com", "consent": "1",
                "photo": (png_bytes(), "street.png")}
        data.update(over)
        return self.client.post("/request", data=data, content_type="multipart/form-data")

    def approve(self, goal="2000", min_c="100"):
        token = self.login()
        with self.app.app_context():
            rid = query("SELECT id FROM requests ORDER BY id DESC", one=True)["id"]
        self.client.post(f"/admin/requests/{rid}", data={
            "_csrf": token, "action": "save_quote", "line_items": "Verge cut | 1200\nDrains | 800",
            "once_amount": "", "monthly_amount": "500", "scope": "Cut and clear."})
        r = self.client.post(f"/admin/requests/{rid}", data={
            "_csrf": token, "action": "approve", "service_type": "once", "goal_amount": goal,
            "budget_amount": "2500", "min_contribution": min_c, "funding_days": "14", "title": "Oak Avenue"})
        self.assertEqual(r.status_code, 302)
        with self.app.app_context():
            return dict(query("SELECT * FROM projects ORDER BY id DESC", one=True)), token

    def contribute(self, slug, amount, token, email="n@example.com", follow=True):
        return self.client.post(f"/projects/{slug}/contribute", data={
            "_csrf": token, "amount_custom": amount, "name": "Neighbour", "email": email, "agree": "1"},
            follow_redirects=False)


class RequestFlowTests(Base):
    def test_request_validation_and_submission(self):
        r = self.submit_request(email="bad", consent="")
        html = r.get_data(as_text=True)
        self.assertIn("valid email", html)
        self.assertIn("Please agree", html)
        r = self.submit_request()
        self.assertEqual(r.status_code, 302)
        self.assertIn("/track/", r.headers["Location"])
        with self.app.app_context():
            req = query("SELECT * FROM requests", one=True)
            self.assertTrue(req["ref"].startswith("CMS-"))
            self.assertTrue(req["photo"].endswith(".jpg"))
        page = self.client.get(r.headers["Location"]).get_data(as_text=True)
        self.assertIn("we’ve got your request", page)

    def test_csrf_is_enforced(self):
        r = self.client.post("/request", data={"street_name": "x"})
        self.assertEqual(r.status_code, 400)

    def test_revision_round_trip(self):
        self.submit_request()
        token = self.login()
        with self.app.app_context():
            req = query("SELECT * FROM requests", one=True)
        self.client.post(f"/admin/requests/{req['id']}", data={"_csrf": token, "action": "revise",
                                                                "note": "Which section of the road?"})
        page = self.client.get(f"/track/{req['token']}").get_data(as_text=True)
        self.assertIn("Which section of the road?", page)
        self.client.post(f"/track/{req['token']}", data={
            "_csrf": token, "street_name": "Oak Avenue (1st to 5th)", "suburb": "Parkview", "city": "Johannesburg",
            "postcode": "2193", "description": "Updated.", "service_pref": "monthly"},
            content_type="multipart/form-data")
        with self.app.app_context():
            req2 = query("SELECT * FROM requests", one=True)
        self.assertEqual(req2["status"], "submitted")
        self.assertEqual(req2["street_name"], "Oak Avenue (1st to 5th)")

    def test_reject(self):
        self.submit_request()
        token = self.login()
        with self.app.app_context():
            rid = query("SELECT id FROM requests", one=True)["id"]
        self.client.post(f"/admin/requests/{rid}", data={"_csrf": token, "action": "reject", "note": "Out of area"})
        with self.app.app_context():
            self.assertEqual(query("SELECT status FROM requests", one=True)["status"], "rejected")


class FundingFlowTests(Base):
    def test_full_funding_flow_in_demo_mode(self):
        self.submit_request()
        p, token = self.approve(goal="2000", min_c="100")
        self.assertEqual(p["status"], "funding")
        self.assertEqual(p["goal_cents"], 200000)
        self.assertEqual(p["min_contribution_cents"], 10000)
        self.assertEqual(p["budget_cents"], 250000)
        # below minimum is refused
        r = self.contribute(p["slug"], "50", token)
        self.assertIn(f"/projects/{p['slug']}", r.headers["Location"])
        with self.app.app_context():
            self.assertEqual(query("SELECT COUNT(*) n FROM contributions", one=True)["n"], 0)
        # valid contribution goes to the demo checkout
        r = self.contribute(p["slug"], "1500", token)
        self.assertIn("/pay/demo/", r.headers["Location"])
        demo_token = r.headers["Location"].rsplit("/", 1)[1]
        r = self.client.post(f"/pay/demo/{demo_token}", data={"_csrf": token, "outcome": "success"})
        result = self.client.get(r.headers["Location"]).get_data(as_text=True)
        self.assertIn("Thank you", result)
        # second contribution completes the goal
        r = self.contribute(p["slug"], "600", token, email="m@example.com")
        t2 = r.headers["Location"].rsplit("/", 1)[1]
        self.client.post(f"/pay/demo/{t2}", data={"_csrf": token, "outcome": "success"})
        with self.app.app_context():
            proj = query("SELECT * FROM projects WHERE id=?", (p["id"],), one=True)
            self.assertEqual(proj["status"], "funded")
        # funded projects refuse more money
        r = self.contribute(p["slug"], "200", token)
        self.client.get(r.headers["Location"])
        with self.app.app_context():
            self.assertEqual(query("SELECT COUNT(*) n FROM contributions WHERE status='paid'", one=True)["n"], 2)
        # status bar on public page
        page = self.client.get(f"/projects/{p['slug']}").get_data(as_text=True)
        self.assertIn("Fully funded", page)

    def test_failed_payment_and_cancel(self):
        self.submit_request()
        p, token = self.approve()
        r = self.contribute(p["slug"], "300", token)
        t = r.headers["Location"].rsplit("/", 1)[1]
        self.client.post(f"/pay/demo/{t}", data={"_csrf": token, "outcome": "fail"})
        with self.app.app_context():
            self.assertEqual(query("SELECT status FROM contributions", one=True)["status"], "failed")

    def test_expired_project_refunds_everyone(self):
        self.submit_request()
        p, token = self.approve(goal="5000")
        r = self.contribute(p["slug"], "500", token)
        t = r.headers["Location"].rsplit("/", 1)[1]
        self.client.post(f"/pay/demo/{t}", data={"_csrf": token, "outcome": "success"})
        with self.app.app_context():
            execute("UPDATE projects SET deadline='2020-01-01' WHERE id=?", (p["id"],))
        self.assertEqual(self.client.get("/tasks/process-expired?key=wrong").status_code, 404)
        r = self.client.get("/tasks/process-expired?key=cron123")
        self.assertEqual(r.status_code, 200)
        with self.app.app_context():
            self.assertEqual(query("SELECT status FROM projects WHERE id=?", (p["id"],), one=True)["status"], "failed")
            self.assertEqual(query("SELECT status FROM contributions", one=True)["status"], "refunded")

    def test_admin_cancel_refunds(self):
        self.submit_request()
        p, token = self.approve(goal="5000")
        r = self.contribute(p["slug"], "500", token)
        t = r.headers["Location"].rsplit("/", 1)[1]
        self.client.post(f"/pay/demo/{t}", data={"_csrf": token, "outcome": "success"})
        self.client.post(f"/admin/projects/{p['id']}", data={"_csrf": token, "action": "cancel", "reason": "Municipality cleaned it"})
        with self.app.app_context():
            self.assertEqual(query("SELECT status FROM contributions", one=True)["status"], "refunded")
        self.assertIn("Every contributor has been refunded", self.client.get(f"/projects/{p['slug']}").get_data(as_text=True))

    def test_media_status_and_monthly_round(self):
        self.submit_request()
        p, token = self.approve()
        self.client.post(f"/admin/projects/{p['id']}", data={
            "_csrf": token, "action": "add_photos", "phase": "before", "title": "Before",
            "photos": [(png_bytes(), "a.png"), (png_bytes(), "b.png")]}, content_type="multipart/form-data")
        self.client.post(f"/admin/projects/{p['id']}", data={
            "_csrf": token, "action": "add_video", "youtube_url": "https://youtu.be/dQw4w9WgXcQ", "phase": "during"})
        self.client.post(f"/admin/projects/{p['id']}", data={
            "_csrf": token, "action": "add_video", "youtube_url": "https://example.com/video"})
        with self.app.app_context():
            kinds = [r["kind"] for r in query("SELECT kind FROM updates WHERE project_id=?", (p["id"],))]
        self.assertEqual(sorted(kinds), ["photo", "photo", "video"])
        page = self.client.get(f"/projects/{p['slug']}").get_data(as_text=True)
        self.assertIn('data-yt="dQw4w9WgXcQ"', page)
        for st in ("funded", "scheduled", "in_progress", "completed"):
            self.client.post(f"/admin/projects/{p['id']}", data={"_csrf": token, "action": "set_status", "status": st})
        with self.app.app_context():
            proj = query("SELECT * FROM projects WHERE id=?", (p["id"],), one=True)
            self.assertEqual(proj["status"], "completed")
            self.assertIsNotNone(proj["completed_at"])
            execute("UPDATE projects SET service_type='monthly' WHERE id=?", (p["id"],))
        r = self.client.post(f"/admin/projects/{p['id']}", data={"_csrf": token, "action": "next_round"})
        with self.app.app_context():
            nxt = query("SELECT * FROM projects ORDER BY id DESC", one=True)
        self.assertEqual(nxt["round_no"], 2)
        self.assertEqual(nxt["parent_id"], p["id"])
        self.assertEqual(nxt["status"], "funding")


class AdminTests(Base):
    def test_admin_requires_login(self):
        self.assertEqual(self.client.get("/admin/").status_code, 302)
        self.login()
        for path in ("/admin/", "/admin/requests", "/admin/projects", "/admin/payments", "/admin/sponsors",
                     "/admin/settings", "/admin/payments?format=csv"):
            self.assertEqual(self.client.get(path).status_code, 200, path)

    def test_bad_password(self):
        token = self.csrf("/admin/login")
        r = self.client.post("/admin/login", data={"_csrf": token, "email": "a@x.co.za", "password": "nope"})
        self.assertIn("don’t match", r.get_data(as_text=True))

    def test_sponsors_crud_and_homepage(self):
        token = self.login()
        for name in ("Alpha", "Beta"):
            self.client.post("/admin/sponsors", data={"_csrf": token, "action": "create", "name": name,
                                                      "url": "alpha.co.za", "logo": (png_bytes(), "l.png")},
                             content_type="multipart/form-data")
        with self.app.app_context():
            beta = query("SELECT * FROM sponsors WHERE name='Beta'", one=True)
            self.assertEqual(query("SELECT url FROM sponsors WHERE name='Alpha'", one=True)["url"], "https://alpha.co.za")
        self.client.post("/admin/sponsors", data={"_csrf": token, "action": "up", "sponsor_id": beta["id"]})
        with self.app.app_context():
            order = [r["name"] for r in query("SELECT name FROM sponsors ORDER BY sort_order")]
        self.assertEqual(order, ["Beta", "Alpha"])
        home = self.client.get("/").get_data(as_text=True)
        self.assertLess(home.index('alt="Beta"'), home.index('alt="Alpha"'))

    def test_homepage_video_setting(self):
        token = self.login()
        self.client.post("/admin/settings", data={"_csrf": token, "hero_video_url": "not a link"})
        self.assertNotIn("data-yt=", self.client.get("/").get_data(as_text=True))
        self.client.post("/admin/settings", data={"_csrf": token,
                                                  "hero_video_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=3",
                                                  "default_min_contribution": "75"})
        home = self.client.get("/").get_data(as_text=True)
        self.assertIn('data-yt="dQw4w9WgXcQ"', home)


class YocoTests(Base):
    payment_mode = "yoco"

    def sign(self, body, msg_id="msg_1", ts=None):
        ts = str(int(ts or time.time()))
        key = base64.b64decode(WEBHOOK_SECRET.split("_", 1)[1])
        sig = base64.b64encode(hmac.new(key, f"{msg_id}.{ts}.".encode() + body, hashlib.sha256).digest()).decode()
        return {"webhook-id": msg_id, "webhook-timestamp": ts, "webhook-signature": f"v1,{sig}"}

    def test_checkout_webhook_and_refund(self):
        self.submit_request()
        p, token = self.approve(goal="2000")
        fake = mock.Mock(status_code=200, content=b"x")
        fake.json.return_value = {"id": "ch_123", "redirectUrl": "https://pay.yoco.com/ch_123", "status": "created"}
        with mock.patch("cms.payments.requests.request", return_value=fake) as m:
            r = self.contribute(p["slug"], "500", token)
        self.assertEqual(r.headers["Location"], "https://pay.yoco.com/ch_123")
        sent = m.call_args.kwargs["json"]
        self.assertEqual(sent["amount"], 50000)
        self.assertEqual(sent["currency"], "ZAR")
        self.assertIn("Idempotency-Key", m.call_args.kwargs["headers"])

        body = json.dumps({"type": "payment.succeeded", "payload": {"id": "p_1", "metadata": {"checkoutId": "ch_123"}}}).encode()
        bad = self.client.post("/webhooks/yoco", data=body, headers={**self.sign(body), "webhook-signature": "v1,AAAA"})
        self.assertEqual(bad.status_code, 401)
        stale = self.client.post("/webhooks/yoco", data=body, headers=self.sign(body, ts=time.time() - 3600))
        self.assertEqual(stale.status_code, 401)
        ok = self.client.post("/webhooks/yoco", data=body, headers=self.sign(body))
        self.assertEqual(ok.status_code, 200)
        dup = self.client.post("/webhooks/yoco", data=body, headers=self.sign(body))
        self.assertEqual(dup.get_data(as_text=True), "duplicate")
        with self.app.app_context():
            c = query("SELECT * FROM contributions", one=True)
            self.assertEqual(c["status"], "paid")
            self.assertEqual(c["payment_id"], "p_1")

        refund_resp = mock.Mock(status_code=200, content=b"x")
        refund_resp.json.return_value = {"id": "ch_123", "refundId": "rf_1", "status": "succeeded"}
        with mock.patch("cms.payments.requests.request", return_value=refund_resp) as m:
            self.client.post(f"/admin/projects/{p['id']}", data={"_csrf": token, "action": "refund", "contribution_id": c["id"]})
        self.assertTrue(m.call_args.args[1].endswith("/checkouts/ch_123/refund"))
        with self.app.app_context():
            self.assertEqual(query("SELECT status FROM contributions", one=True)["status"], "refunded")

    def test_return_page_reconciles_with_yoco(self):
        self.submit_request()
        p, token = self.approve(goal="2000")
        fake = mock.Mock(status_code=200, content=b"x")
        fake.json.return_value = {"id": "ch_9", "redirectUrl": "https://pay.yoco.com/ch_9"}
        with mock.patch("cms.payments.requests.request", return_value=fake):
            self.contribute(p["slug"], "500", token)
        with self.app.app_context():
            rt = query("SELECT return_token FROM contributions", one=True)["return_token"]
        status = mock.Mock(status_code=200, content=b"x")
        status.json.return_value = {"id": "ch_9", "status": "completed", "paymentId": "p_9"}
        with mock.patch("cms.payments.requests.request", return_value=status):
            page = self.client.get(f"/pay/return/{rt}").get_data(as_text=True)
        self.assertIn("Thank you", page)

    def test_yoco_error_is_handled(self):
        self.submit_request()
        p, token = self.approve()
        err = mock.Mock(status_code=500, content=b"x", text="boom")
        err.json.side_effect = ValueError
        with mock.patch("cms.payments.requests.request", return_value=err):
            r = self.contribute(p["slug"], "500", token)
        self.assertIn("#contribute", r.headers["Location"])


class UtilTests(unittest.TestCase):
    def test_youtube_ids(self):
        for url in ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "https://youtu.be/dQw4w9WgXcQ?si=abc",
                    "https://www.youtube.com/shorts/dQw4w9WgXcQ", "youtube.com/embed/dQw4w9WgXcQ", "dQw4w9WgXcQ"):
            self.assertEqual(youtube_id(url), "dQw4w9WgXcQ", url)
        for url in ("https://vimeo.com/123", "https://youtube.com/watch?v=short", "", None,
                    "https://evil.com/watch?v=dQw4w9WgXcQ"):
            self.assertIsNone(youtube_id(url), url)

    def test_money(self):
        self.assertEqual(parse_rand("R1 250"), 125000)
        self.assertEqual(parse_rand("1,250.50"), 125050)
        self.assertEqual(rand(125000), "R1 250")


if __name__ == "__main__":
    unittest.main()
