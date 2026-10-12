"""Email templates, delivery and the admin email editor."""
import re
import unittest
from email import message_from_bytes
from unittest import mock

from cms.db import execute, query
from test_flows import Base, png_bytes


class FakeSMTP:
    outbox = []

    def __init__(self, *a, **k):
        pass

    def starttls(self, **k):
        pass

    def login(self, *a):
        pass

    def send_message(self, msg):
        FakeSMTP.outbox.append(msg)

    def quit(self):
        pass


def parts(msg):
    text = msg.get_body(("plain",)).get_content()
    html = msg.get_body(("html",)).get_content()
    return text, html


class EmailTests(Base):
    def setUp(self):
        super().setUp()
        self.app.config.update(SMTP_HOST="smtp.test", SMTP_PORT="587", ADMIN_NOTIFY_EMAIL="ops@x.co.za")
        FakeSMTP.outbox = []
        patcher = mock.patch("smtplib.SMTP", FakeSMTP)
        patcher.start()
        self.addCleanup(patcher.stop)

    def sent_to(self, addr):
        return [m for m in FakeSMTP.outbox if m["To"] == addr]

    def test_requester_is_kept_updated_through_the_whole_journey(self):
        self.submit_request()
        mine = self.sent_to("ryan@example.com")
        self.assertEqual(len(mine), 1)
        self.assertIn("We’ve received your request", mine[0]["Subject"])
        text, html = parts(mine[0])
        self.assertRegex(html, r'href="http://localhost/track/[\w-]+"')
        self.assertIn("Track your request: http://localhost/track/", text)
        self.assertIn("Hi" if False else "Thanks, Ryan.", html)
        alert = self.sent_to("ops@x.co.za")
        self.assertEqual(len(alert), 1)
        self.assertIn("/admin/requests/", parts(alert[0])[1])

        # revision → requester edits → confirmation
        token = self.login()
        with self.app.app_context():
            req = query("SELECT * FROM requests", one=True)
        self.client.post(f"/admin/requests/{req['id']}", data={"_csrf": token, "action": "revise",
                                                                "note": "Please add the drain photo"})
        self.assertIn("Please add the drain photo", parts(self.sent_to("ryan@example.com")[-1])[1])
        t = self.csrf(f"/track/{req['token']}")
        self.client.post(f"/track/{req['token']}", data={"_csrf": t, "street_name": "Oak Avenue", "city": "Johannesburg",
                                                          "postcode": "2193", "description": "More detail",
                                                          "service_pref": "once"})
        self.assertIn("got your changes", self.sent_to("ryan@example.com")[-1]["Subject"])

        # approve → funding link
        p, token = self.approve(goal="2000")
        last = self.sent_to("ryan@example.com")[-1]
        self.assertIn("is live", last["Subject"])
        self.assertIn(f"/projects/{p['slug']}", parts(last)[1])
        self.assertIn("R2 000", parts(last)[1].replace("\xa0", " "))

        # contribution → receipt; goal reached → backers AND requester hear about it
        r = self.contribute(p["slug"], "2000", token, email="nina@example.com")
        tok = r.headers["Location"].rsplit("/", 1)[1]
        self.client.post(f"/pay/demo/{tok}", data={"_csrf": token, "outcome": "success"})
        subjects = [m["Subject"] for m in self.sent_to("nina@example.com")]
        self.assertTrue(any("Thank you for backing" in s for s in subjects))
        self.assertTrue(any("fully funded" in s for s in subjects))
        self.assertTrue(any("fully funded" in m["Subject"] for m in self.sent_to("ryan@example.com")))
        self.assertTrue(any("Funded:" in m["Subject"] for m in self.sent_to("ops@x.co.za")))

        # status changes reach everyone; unticking the box sends nothing
        before = len(FakeSMTP.outbox)
        self.client.post(f"/admin/projects/{p['id']}", data={"_csrf": token, "action": "set_status",
                                                             "status": "scheduled", "has_notify": "1"})
        self.assertEqual(len(FakeSMTP.outbox), before)
        self.client.post(f"/admin/projects/{p['id']}", data={"_csrf": token, "action": "set_status",
                                                             "status": "in_progress", "has_notify": "1",
                                                             "notify": "1"})
        self.assertEqual({m["To"] for m in FakeSMTP.outbox[before:]}, {"ryan@example.com", "nina@example.com"})

        # photo update with email → includes the photo
        self.client.post(f"/admin/projects/{p['id']}", data={"_csrf": token, "action": "add_photos", "phase": "during",
                                                             "title": "Drains cleared", "notify": "1",
                                                             "photos": (png_bytes(), "a.png")},
                         content_type="multipart/form-data")
        upd = self.sent_to("nina@example.com")[-1]
        self.assertIn("Drains cleared", parts(upd)[1])
        self.assertRegex(parts(upd)[1], r'<img src="http://localhost/static/uploads/update-')

        # history shows on the admin pages
        self.assertIn("Drains cleared", self.client.get(f"/admin/projects/{p['id']}").get_data(as_text=True))
        self.assertIn("got your changes", self.client.get(f"/admin/requests/{req['id']}").get_data(as_text=True))

    def test_failed_project_emails_requester_and_refunds_backers(self):
        self.submit_request()
        p, token = self.approve(goal="5000")
        r = self.contribute(p["slug"], "200", token, email="b@example.com")
        self.client.post(f"/pay/demo/{r.headers['Location'].rsplit('/', 1)[1]}", data={"_csrf": token, "outcome": "success"})
        self.client.post(f"/admin/projects/{p['id']}", data={"_csrf": token, "action": "cancel", "reason": "Road works"})
        self.assertTrue(any("refund" in m["Subject"].lower() for m in self.sent_to("b@example.com")))
        closed = [m for m in self.sent_to("ryan@example.com") if "has closed" in m["Subject"]]
        self.assertEqual(len(closed), 1)
        self.assertIn("road works", parts(closed[0])[1])

    def test_deadline_reminder_sent_once(self):
        self.submit_request()
        p, token = self.approve(goal="5000")
        from cms.utils import today_sast
        from datetime import timedelta
        with self.app.app_context():
            execute("UPDATE projects SET deadline=? WHERE id=?", ((today_sast() + timedelta(days=2)).isoformat(), p["id"]))
        for _ in range(2):
            self.client.get("/tasks/process-expired?key=cron123")
        reminders = [m for m in self.sent_to("ryan@example.com") if "days left" in m["Subject"]]
        self.assertEqual(len(reminders), 1)
        self.assertIn("2 days left", reminders[0]["Subject"])

    def test_admin_edits_template_and_it_is_used(self):
        token = self.login()
        page = self.client.get("/admin/emails").get_data(as_text=True)
        for label in ("Request received", "Clean completed", "Deadline reminder", "Team alerts", "Email log"):
            self.assertIn(label, page)
        edit = self.client.get("/admin/emails/request_received").get_data(as_text=True)
        self.assertIn("track_url", edit)
        r = self.client.post("/admin/emails/request_received", data={
            "_csrf": token, "action": "save", "subject": "Howzit {{ name }} – {{ ref }}",
            "html": '<h1 style="color:green">Custom for {{ street }}</h1>{{ button(track_url, "Go") }}'})
        self.assertEqual(r.status_code, 302)
        self.submit_request(requester_name="<b>Evil</b> Name")
        msg = self.sent_to("ryan@example.com")[-1]
        self.assertTrue(msg["Subject"].startswith("Howzit <b>Evil</b>"))  # subject is plain text
        text, html = parts(msg)
        self.assertIn("Custom for Oak Avenue", html)
        self.assertIn("CLEAN MY", html)  # still wrapped in the layout
        self.assertIn("Go: http://localhost/track/", text)
        # reset brings the default back
        self.client.post("/admin/emails/request_received", data={"_csrf": token, "action": "reset"})
        with self.app.app_context():
            self.assertIsNone(query("SELECT * FROM email_templates WHERE key='request_received'", one=True))

    def test_names_are_escaped_in_html(self):
        self.submit_request(requester_name='<script>alert(1)</script> Bob')
        html = parts(self.sent_to("ryan@example.com")[-1])[1]
        self.assertNotIn("<script>alert", html)

    def test_bad_and_unsafe_templates_are_rejected(self):
        token = self.login()
        for bad in ("{% if %}", "{{ ''.__class__.__mro__[1].__subclasses__() }}",
                    "{{ button.__globals__ }}", "{{ cycler.__init__.__globals__.os }}"):
            r = self.client.post("/admin/emails/request_received", data={"_csrf": token, "action": "save",
                                                                         "subject": "x", "html": bad})
            self.assertEqual(r.status_code, 400, bad)
            self.assertIn("Template error", r.get_data(as_text=True))
        with self.app.app_context():
            self.assertIsNone(query("SELECT * FROM email_templates", one=True))
        r = self.client.post("/admin/emails/layout", data={"_csrf": token, "action": "save", "html": "<p>no slot</p>"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.client.post("/admin/emails/nope", data={"_csrf": token}).status_code, 404)

    def test_layout_edit_applies_to_every_email(self):
        token = self.login()
        r = self.client.post("/admin/emails/layout", data={"_csrf": token, "action": "save",
                                                           "html": "<div>BRANDED</div>{{ content }}<p>FOOT {{ contact_email }}</p>"})
        self.assertEqual(r.status_code, 302)
        self.submit_request()
        html = parts(self.sent_to("ryan@example.com")[-1])[1]
        self.assertIn("BRANDED", html)
        self.assertIn("FOOT info@cleanmystreet.co.za", html)

    def test_turning_an_email_off(self):
        token = self.login()
        self.client.post("/admin/emails", data={"_csrf": token, "action": "toggle", "key": "request_received"})
        self.submit_request()
        self.assertEqual(self.sent_to("ryan@example.com"), [])
        with self.app.app_context():
            row = query("SELECT * FROM email_log WHERE template='request_received'", one=True)
        self.assertEqual(row["status"], "off")
        self.client.post("/admin/emails", data={"_csrf": token, "action": "toggle", "key": "request_received"})
        self.submit_request()
        self.assertEqual(len(self.sent_to("ryan@example.com")), 1)

    def test_preview_and_test_send(self):
        token = self.login()
        r = self.client.get("/admin/emails/project_update/preview")
        self.assertEqual(r.status_code, 200)
        self.assertIn("sandbox", r.headers["Content-Security-Policy"])
        self.assertIn("Halfway there", r.get_data(as_text=True))
        r = self.client.post("/admin/emails/project_update/preview", data={"_csrf": token, "subject": "S",
                                                                         "html": "<p>Live {{ project_title }}</p>"})
        self.assertIn("Live Beyers Naud", r.get_data(as_text=True))
        r = self.client.post("/admin/emails/project_update/preview", data={"_csrf": token, "subject": "S", "html": "{% if %}"})
        self.assertIn("This template has a problem", r.get_data(as_text=True))
        self.assertEqual(self.client.get("/admin/emails/layout/preview").status_code, 200)
        r = self.client.post("/admin/emails/project_completed", data={"_csrf": token, "action": "test",
                                                                      "subject": "Hello {{ name }}", "html": "<p>Test body</p>"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(FakeSMTP.outbox[-1]["To"], "a@x.co.za")
        self.assertEqual(FakeSMTP.outbox[-1]["Subject"], "[Test] Hello Thandi")
        with self.app.app_context():
            self.assertIsNone(query("SELECT * FROM email_templates", one=True))  # test doesn't save
        # every default template renders
        for key in ("request_received", "request_updated", "request_revision", "request_approved", "request_rejected",
                    "deadline_reminder", "project_closed", "contribution_receipt", "project_funded",
                    "project_scheduled", "project_in_progress", "project_completed", "project_update", "refund",
                    "admin_new_request", "admin_funded", "layout"):
            self.assertEqual(self.client.get(f"/admin/emails/{key}").status_code, 200, key)
            body = self.client.get(f"/admin/emails/{key}/preview").get_data(as_text=True)
            self.assertNotIn("problem", body, key)
        self.assertEqual(self.client.get("/admin/emails/request_received/preview",
                                         headers={"Cookie": ""}).status_code, 200)

    def test_team_alert_addresses(self):
        token = self.login()
        self.client.post("/admin/emails", data={"_csrf": token, "action": "alerts", "admin_notify_email": "x@y.co.za, z@y.co.za"})
        self.submit_request()
        self.assertEqual(len(self.sent_to("x@y.co.za")), 1)
        self.assertEqual(len(self.sent_to("z@y.co.za")), 1)
        r = self.client.post("/admin/emails", data={"_csrf": token, "action": "alerts", "admin_notify_email": "nope"},
                             follow_redirects=True)
        self.assertIn("Not a valid email", r.get_data(as_text=True))

    def test_smtp_failure_never_breaks_the_request(self):
        class Boom(FakeSMTP):
            def send_message(self, msg):
                raise OSError("mail server down")
        with mock.patch("smtplib.SMTP", Boom):
            r = self.submit_request()
        self.assertEqual(r.status_code, 302)
        with self.app.app_context():
            row = query("SELECT * FROM email_log WHERE to_addr='ryan@example.com'", one=True)
        self.assertEqual(row["status"], "failed")
        self.assertIn("mail server down", row["error"])

    def test_emails_need_login(self):
        for path in ("/admin/emails", "/admin/emails/layout", "/admin/emails/layout/preview"):
            self.assertEqual(self.client.get(path).status_code, 302, path)


class SweepTests(Base):
    def test_page_views_run_the_hourly_sweep_without_cron(self):
        from cms import public
        from cms.db import execute as ex
        self.submit_request()
        p, token = self.approve(goal="5000")
        with self.app.app_context():
            ex("UPDATE projects SET deadline='2020-01-01' WHERE id=?", (p["id"],))
        self.app.testing = False
        public._last_sweep["t"] = 0
        try:
            self.assertEqual(self.client.get("/").status_code, 200)
        finally:
            self.app.testing = True
        with self.app.app_context():
            self.assertEqual(query("SELECT status FROM projects WHERE id=?", (p["id"],), one=True)["status"], "failed")
            self.assertTrue(float(query("SELECT value FROM settings WHERE key='last_sweep'", one=True)["value"]) > 0)

    def test_vercel_cron_header(self):
        self.assertEqual(self.client.get("/tasks/process-expired").status_code, 404)
        self.assertEqual(self.client.get("/tasks/process-expired", headers={"Authorization": "Bearer nope"}).status_code, 404)
        self.assertEqual(self.client.get("/tasks/process-expired", headers={"Authorization": "Bearer cron123"}).status_code, 200)


class MailServerProblemTests(Base):
    def setUp(self):
        super().setUp()
        self.app.config.update(SMTP_HOST="mail.example.test", SMTP_PORT="587")

    def test_expired_certificate_gives_clear_advice(self):
        import ssl

        class Expired(FakeSMTP):
            def starttls(self, context=None, **k):
                FakeSMTP.contexts.append(context)
                if context.verify_mode != ssl.CERT_NONE:
                    raise ssl.SSLCertVerificationError(
                        1, "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: certificate has expired (_ssl.c:1010)")
        FakeSMTP.contexts, FakeSMTP.outbox = [], []
        token = self.login()
        with mock.patch("smtplib.SMTP", Expired):
            r = self.client.post("/admin/emails/project_completed", data={"_csrf": token, "action": "test",
                                                                          "subject": "Hi", "html": "<p>x</p>"})
            page = r.get_data(as_text=True)
            self.assertIn("certificate has expired", page)
            self.assertIn("SMTP_TLS_VERIFY=false", page)
            r = self.client.post("/admin/emails", data={"_csrf": token, "action": "check_smtp"}, follow_redirects=True)
            self.assertIn("certificate has expired", r.get_data(as_text=True))
            # a real send logs the same advice and never breaks the request
            self.assertEqual(self.submit_request().status_code, 302)
            with self.app.app_context():
                row = query("SELECT * FROM email_log WHERE template='request_received'", one=True)
            self.assertEqual(row["status"], "failed")
            self.assertIn("certificate has expired", row["error"])
            # workaround switch: still encrypted, certificate not checked
            self.app.config["SMTP_TLS_VERIFY"] = False
            r = self.client.post("/admin/emails", data={"_csrf": token, "action": "check_smtp"}, follow_redirects=True)
            self.assertIn("Connected to mail.example.test", r.get_data(as_text=True))
            self.assertIn("Certificate checking is off", r.get_data(as_text=True))
            self.submit_request()
            self.assertEqual(FakeSMTP.outbox[-1]["To"], "ryan@example.com")

    def test_wrong_password_message(self):
        import smtplib

        class BadLogin(FakeSMTP):
            def login(self, *a):
                raise smtplib.SMTPAuthenticationError(535, b"bad")
        self.app.config.update(SMTP_USER="x", SMTP_PASSWORD="y")
        token = self.login()
        with mock.patch("smtplib.SMTP", BadLogin):
            r = self.client.post("/admin/emails", data={"_csrf": token, "action": "check_smtp"}, follow_redirects=True)
        self.assertIn("rejected SMTP_USER / SMTP_PASSWORD", r.get_data(as_text=True))
