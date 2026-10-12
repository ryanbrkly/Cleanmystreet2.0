"""Job costing: our cost vs the charge to neighbours."""
import unittest

from cms import costing
from cms.db import query
from test_flows import Base


class MathTests(unittest.TestCase):
    def c(self, **kw):
        c = costing.defaults({})
        c.update(contingency_pct=0, overhead_pct=0, markup_pct=0, vat=False, fee_pct=0, round_to=0)
        c.update(kw)
        return c

    def test_basic_markup_and_rounding(self):
        c = self.c(lines=[{"cat": "labour", "desc": "Workers", "qty": 8, "unit": "day", "rate_cents": 35000},
                          {"cat": "disposal", "desc": "Dumping", "qty": 2, "unit": "load", "rate_cents": 45000}],
                   contingency_pct=5, overhead_pct=10, markup_pct=30, round_to=10)
        r = costing.compute(c)["once"]
        self.assertEqual(r["direct"], 370000)
        self.assertEqual(r["contingency"], 18500)
        self.assertEqual(r["overhead"], 38850)
        self.assertEqual(r["cost"], 427350)
        self.assertEqual(r["charge"], 556000)          # R5 555.55 → up to the next R10
        self.assertEqual(r["profit"], 556000 - 427350)
        self.assertEqual(r["by_cat"], {"labour": 280000, "disposal": 90000})

    def test_vat_fees_override_and_loss(self):
        c = self.c(lines=[{"cat": "labour", "desc": "x", "qty": 1, "unit": "", "rate_cents": 100000}],
                   markup_pct=20, vat=True, fee_pct=3)
        r = costing.compute(c)["once"]
        self.assertEqual(r["charge"], 138000)           # 1000 × 1.2 × 1.15
        self.assertEqual(r["excl"], 120000)
        self.assertEqual(r["vat"], 18000)
        self.assertEqual(r["fees"], 4140)
        self.assertEqual(r["profit"], 120000 - 100000 - 4140)
        c["price_override_cents"] = 100000
        r = costing.compute(c)["once"]
        self.assertLess(r["profit"], 0)

    def test_monthly_and_public_lines_add_up(self):
        c = self.c(lines=[{"cat": "labour", "desc": "a", "qty": 3, "unit": "", "rate_cents": 33333},
                          {"cat": "materials", "desc": "b", "qty": 1, "unit": "", "rate_cents": 12345},
                          {"cat": "transport", "desc": "c", "qty": 7, "unit": "", "rate_cents": 600}],
                   markup_pct=35, vat=True, round_to=50, monthly_on=True, monthly_visits=2, monthly_factor_pct=50)
        res = costing.compute(c)
        lines = costing.public_lines(res, True)
        self.assertEqual(sum(i["cents"] for i in lines), res["once"]["charge"])
        self.assertEqual(lines[-1]["label"], "VAT (15%)")
        self.assertEqual(res["monthly"]["cost"], round(res["once"]["cost"] * 0.5 * 2))

    def test_rate_card_parsing(self):
        items = costing.rate_card("labour | Worker | day | 350\nnonsense\nweird | Thing | each | 1,5")
        self.assertEqual(items[0], {"cat": "labour", "desc": "Worker", "unit": "day", "rate_cents": 35000})
        self.assertEqual(items[1]["cat"], "other")
        self.assertEqual(items[1]["rate_cents"], 150)


class CostingFlowTests(Base):
    def costing_form(self, token, **extra):
        data = {"_csrf": token, "action": "save_quote",
                "line_cat": ["labour", "equipment", "disposal", "labour"],
                "line_desc": ["General workers", "Brush cutters", "Dumping", ""],
                "line_qty": ["8", "2", "2", ""], "line_unit": ["worker-day", "machine-day", "load", ""],
                "line_rate": ["350", "180", "450", ""],
                "contingency_pct": "5", "overhead_pct": "10", "markup_pct": "30", "fee_pct": "3", "round_to": "10",
                "vat": "1", "auto_public": "1", "monthly_on": "1", "monthly_visits": "1", "monthly_factor_pct": "60",
                "scope": "Verges, drains and pavements."}
        data.update(extra)
        return data

    def test_cost_to_quote_to_project(self):
        self.submit_request()
        token = self.login()
        with self.app.app_context():
            rid = query("SELECT id FROM requests", one=True)["id"]
        page = self.client.get(f"/admin/requests/{rid}").get_data(as_text=True)
        self.assertIn("Costing and quote", page)
        self.assertIn("Add from your rate card", page)
        r = self.client.post(f"/admin/requests/{rid}", data=self.costing_form(token), follow_redirects=True)
        self.assertIn("Costing saved", r.get_data(as_text=True))
        with self.app.app_context():
            q = dict(query("SELECT * FROM quotes", one=True))
        expect = costing.compute(costing.load(q["costing"], {}))
        self.assertEqual(q["once_cents"], expect["once"]["charge"])
        self.assertEqual(q["cost_cents"], expect["once"]["cost"])
        self.assertGreater(q["monthly_cents"], 0)
        import json
        public = json.loads(q["line_items"])
        self.assertEqual(sum(i["cents"] for i in public), q["once_cents"])
        self.assertNotIn("350", json.dumps(public))  # rates never reach the public quote
        # approve: goal = charge, our cost = costing cost, VAT and fees remembered
        self.client.post(f"/admin/requests/{rid}", data={"_csrf": token, "action": "approve", "service_type": "once",
                                                        "min_contribution": "100", "funding_days": "14"})
        with self.app.app_context():
            p = dict(query("SELECT * FROM projects", one=True))
        self.assertEqual(p["goal_cents"], q["once_cents"])
        self.assertEqual(p["budget_cents"], q["cost_cents"])
        self.assertEqual(p["vat_pct"], 15)
        self.assertEqual(p["fee_pct"], 3)
        detail = self.client.get(f"/admin/projects/{p['id']}").get_data(as_text=True)
        self.assertIn("profit at goal", detail)
        self.assertIn("Our cost", self.client.get("/admin/projects").get_data(as_text=True))
        # the public page shows category totals, not our rates or profit
        pub = self.client.get(f"/projects/{p['slug']}").get_data(as_text=True)
        self.assertIn("Crew labour", pub)
        self.assertNotIn("General workers", pub)
        self.assertNotIn("profit", pub.lower())

    def test_loss_warning_and_escaping(self):
        self.submit_request()
        token = self.login()
        with self.app.app_context():
            rid = query("SELECT id FROM requests", one=True)["id"]
        r = self.client.post(f"/admin/requests/{rid}", data=self.costing_form(
            token, price_override="500", line_desc=["<script>x</script>", "Brush cutters", "Dumping", ""]),
            follow_redirects=True)
        html = r.get_data(as_text=True)
        self.assertIn("this job loses", html)
        self.assertNotIn("<script>x</script>", html)
        self.assertIn("is-loss", html)

    def test_manual_public_lines_and_legacy_quote_still_work(self):
        self.submit_request()
        token = self.login()
        with self.app.app_context():
            rid = query("SELECT id FROM requests", one=True)["id"]
        form = self.costing_form(token, line_items="Full street clean | 9999")
        form.pop("auto_public")
        self.client.post(f"/admin/requests/{rid}", data=form)
        with self.app.app_context():
            self.assertIn("Full street clean", query("SELECT line_items FROM quotes", one=True)["line_items"])
        # the old simple quote form still saves
        self.client.post(f"/admin/requests/{rid}", data={"_csrf": token, "action": "save_quote",
                                                        "line_items": "Verges | 1000", "scope": "x"})
        with self.app.app_context():
            self.assertEqual(query("SELECT once_cents FROM quotes", one=True)["once_cents"], 100000)

    def test_settings_defaults_and_rate_card(self):
        token = self.login()
        page = self.client.get("/admin/settings").get_data(as_text=True)
        self.assertIn("Costing defaults", page)
        self.client.post("/admin/settings", data={"_csrf": token, "cost_contingency_pct": "7", "cost_overhead_pct": "12",
                                                  "cost_markup_pct": "40", "cost_fee_pct": "2.9", "cost_round_to": "50",
                                                  "cost_monthly_factor_pct": "55", "cost_vat": "1",
                                                  "cost_rate_card": "labour | Crew lead | day | 700\nbad line"})
        self.submit_request()
        with self.app.app_context():
            rid = query("SELECT id FROM requests", one=True)["id"]
        html = self.client.get(f"/admin/requests/{rid}").get_data(as_text=True)
        self.assertIn('name="markup_pct" value="40"', html)
        self.assertIn('data-desc="Crew lead"', html)
        self.assertNotIn("bad line", html)
