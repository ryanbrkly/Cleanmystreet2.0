"""Site settings: homepage numbers and removing sample data."""
import re
import unittest

from cms.db import query
from cms.seed import seed_demo
from test_flows import Base


class NumbersTests(Base):
    def tally(self):
        html = self.client.get("/").get_data(as_text=True).replace("\xa0", " ")
        return re.findall(r"<b>([^<]+)</b>", html.split('class="tally"')[1].split("</section>")[0])

    def save(self, token, **values):
        data = {"_csrf": token}
        data.update(values)
        return self.client.post("/admin/settings", data=data)

    def test_figures_are_kept_and_shown(self):
        token = self.login()
        self.submit_request()
        p, token = self.approve(goal="1000")
        r = self.contribute(p["slug"], "300", token)
        self.client.post(f"/pay/demo/{r.headers['Location'].rsplit('/', 1)[1]}", data={"_csrf": token, "outcome": "success"})
        self.assertEqual(self.save(token, stats_mode="add", legacy_streets="12", legacy_raised="R50 000",
                                   legacy_contributors="400").status_code, 302)
        self.assertEqual(self.tally()[:3], ["12", "R50 300", "401"])
        page = self.client.get("/admin/settings").get_data(as_text=True)
        self.assertIn('value="12"', page)
        self.assertIn('value="50000"', page)
        self.assertIn('value="400"', page)
        # exact mode shows the typed figures only, and survives saving other settings
        self.save(token, stats_mode="exact", legacy_streets="30", legacy_raised="250000", legacy_contributors="900")
        self.save(token, announcement="Hello")
        self.assertEqual(self.tally()[:3], ["30", "R250 000", "900"])
        self.assertIn('value="exact" checked', self.client.get("/admin/settings").get_data(as_text=True))
        self.assertIn("R250 000", self.client.get("/about").get_data(as_text=True).replace("\xa0", " "))
        # junk input doesn't break the homepage
        self.save(token, legacy_streets="abc", legacy_contributors="-5", legacy_raised="nonsense")
        self.assertEqual(self.client.get("/").status_code, 200)

    def test_remove_sample_data_keeps_real_data(self):
        with self.app.app_context():
            seed_demo()
        token = self.login()
        self.submit_request(email="real@person.co.za", street_name="Real Street")
        page = self.client.get("/admin/settings").get_data(as_text=True)
        self.assertIn("Remove sample data", page)
        self.save(token, action="remove_samples")
        with self.app.app_context():
            self.assertEqual(query("SELECT COUNT(*) n FROM requests WHERE email LIKE 'demo%@example.com'", one=True)["n"], 0)
            self.assertEqual(query("SELECT COUNT(*) n FROM projects", one=True)["n"], 0)
            self.assertEqual(query("SELECT COUNT(*) n FROM contributions", one=True)["n"], 0)
            self.assertEqual(query("SELECT COUNT(*) n FROM sponsors", one=True)["n"], 0)
            self.assertEqual(query("SELECT street_name FROM requests", one=True)["street_name"], "Real Street")
        self.assertNotIn("Remove sample data", self.client.get("/admin/settings").get_data(as_text=True))
        for path in ("/", "/projects", "/sponsors", "/about", "/admin/", "/admin/requests", "/admin/payments"):
            self.assertEqual(self.client.get(path).status_code, 200, path)
