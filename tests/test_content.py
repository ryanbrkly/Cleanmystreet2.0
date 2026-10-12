"""Editable About and Sponsors page content."""
import unittest

from cms.db import query
from test_flows import Base, png_bytes


class ContentTests(Base):
    def test_defaults_render(self):
        about = self.client.get("/about").get_data(as_text=True)
        self.assertIn("Reshaping communities, restoring value", about)
        self.assertIn("Getting to know us", about)
        sp = self.client.get("/sponsors").get_data(as_text=True)
        self.assertIn("Support the businesses that support your street", sp)
        self.assertIn("Four easy ways to back them", sp)

    def test_edit_hide_reset_and_escape(self):
        token = self.login()
        page = self.client.get("/admin/content/about").get_data(as_text=True)
        self.assertIn('name="about.hero_title"', page)
        self.assertIn('name="about.intro_image"', page)
        r = self.client.post("/admin/content/about", data={
            "_csrf": token, "about.hero_title": "Our <b>story</b>",
            "about.intro_body": "First para.\n\nSecond para.", "about.quote": "",
            "about.why2_title": "", "about.where_text": "We work in {areas} and beyond."})
        self.assertEqual(r.status_code, 302)
        about = self.client.get("/about").get_data(as_text=True)
        self.assertIn("Our &lt;b&gt;story&lt;/b&gt;", about)
        self.assertIn("<p>First para.</p><p>Second para.</p>", about)
        self.assertNotIn("quote-band", about)
        self.assertNotIn("Looking after the environment", about)
        self.assertIn("Restoring property value", about)
        self.assertIn("We work in Johannesburg", about)
        # untouched fields aren't stored; reset brings a section back
        with self.app.app_context():
            keys = {r["key"] for r in query("SELECT key FROM page_content")}
        self.assertNotIn("about.hero_lede", keys)
        sections = [t for t, _ in __import__("cms.content", fromlist=["PAGES"]).PAGES["about"]["sections"]]
        self.client.post("/admin/content/about", data={"_csrf": token, "reset_section": str(sections.index("Quote"))})
        self.assertIn("quote-band", self.client.get("/about").get_data(as_text=True))

    def test_picture_upload_and_back_to_original(self):
        token = self.login()
        self.client.post("/admin/content/about", data={"_csrf": token, "about.intro_image": (png_bytes(), "p.png")},
                         content_type="multipart/form-data")
        with self.app.app_context():
            name = query("SELECT value FROM page_content WHERE key='about.intro_image'", one=True)["value"]
        self.assertTrue(name.startswith("page-"))
        self.assertIn(f"/static/uploads/{name}", self.client.get("/about").get_data(as_text=True))
        self.client.post("/admin/content/about", data={"_csrf": token, "about.intro_image__default": "1"},
                         content_type="multipart/form-data")
        self.assertIn("island-rebuild", self.client.get("/about").get_data(as_text=True))
        self.assertEqual(self.client.get(f"/static/uploads/{name}").status_code, 404)

    def test_sponsor_pages(self):
        token = self.login()
        self.client.post("/admin/content/sponsors", data={
            "_csrf": token, "sponsors.hero_title": "Back local", "sponsors.ways3_title": "",
            "sponsors.gets_list": "Logo on site\nThanks on WhatsApp", "sponsor.thanks_heading": "Cheers, {name}!"})
        sp = self.client.get("/sponsors").get_data(as_text=True)
        self.assertIn("Back local", sp)
        self.assertNotIn("Leave a review", sp)
        self.assertIn("Thanks on WhatsApp", sp)
        self.client.post("/admin/sponsors/new", data={"_csrf": token, "name": "Corner Café", "active": "1"})
        self.assertIn("Cheers, Corner Café!", self.client.get("/sponsors/corner-cafe").get_data(as_text=True))

    def test_story_heading_and_home_button(self):
        token = self.login()
        self.client.post("/admin/content/about", data={"_csrf": token, "about.story_heading": "Our process",
                                                       "about.home_button": "See our process"})
        self.assertIn('id="story-title">Our process<', self.client.get("/about").get_data(as_text=True))
        self.assertIn("See our process", self.client.get("/").get_data(as_text=True))
        self.client.post("/admin/content/about", data={"_csrf": token, "about.story_heading": "", "about.home_button": ""})
        about = self.client.get("/about").get_data(as_text=True)
        self.assertNotIn("story-title", about)
        self.assertIn('id="story"', about)
        self.assertNotIn("#story", self.client.get("/").get_data(as_text=True).split("filmstrip__foot")[1][:400])

    def test_needs_login_and_known_page(self):
        self.assertEqual(self.client.get("/admin/content/about").status_code, 302)
        self.login()
        self.assertEqual(self.client.get("/admin/content/nope").status_code, 404)


from test_flows import TursoBackendTests  # noqa: E402


class ContentOnTursoTests(TursoBackendTests):
    def test_everything_runs_on_turso(self):  # replaces the inherited test; only check page content here
        token = self.login()
        r = self.client.post("/admin/content/about", data={"_csrf": token, "about.story_heading": "Our process"})
        self.assertEqual(r.status_code, 302)
        self.assertIn('id="story-title">Our process<', self.client.get("/about").get_data(as_text=True))
        self.client.post("/admin/content/about", data={"_csrf": token, "about.story_heading": "How a street gets done"})
        self.assertIn("How a street gets done", self.client.get("/about").get_data(as_text=True))


class AboutBlocksTests(Base):
    def order(self, html):
        marks = {"intro": "Getting to know us", "vmv": "vmv__tag", "story": 'id="story"', "why": "Why a clean community",
                 "quote": "quote-band", "green": "Going green", "where": "Where we work", "stats": "about-stats__grid"}
        found = [(html.find(m), k) for k, m in marks.items() if m in html]
        return [k for _, k in sorted(found)]

    def layout_form(self, token, ids, off=(), stats=("streets", "raised", "contributors", "active"), **extra):
        data = {"_csrf": token, "action": "save"}
        for i, bid in enumerate(ids, 1):
            data[f"pos_{bid}"] = str(i)
            if bid not in off:
                data[f"on_{bid}"] = "1"
        for k in stats:
            data[f"stat_{k}"] = "1"
        data.update(extra)
        return data

    def test_hide_reorder_and_numbers(self):
        token = self.login()
        default = ["intro", "vmv", "story", "why", "quote", "green", "where", "stats"]
        self.assertEqual(self.order(self.client.get("/about").get_data(as_text=True)), default)
        self.assertIn("Blocks on the About page", self.client.get("/admin/about-layout").get_data(as_text=True))
        # hide numbers and quote, put "where" first
        new = ["where"] + [b for b in default if b != "where"]
        self.client.post("/admin/about-layout", data=self.layout_form(token, new, off=("stats", "quote")))
        self.assertEqual(self.order(self.client.get("/about").get_data(as_text=True)),
                         ["where", "intro", "vmv", "story", "why", "green"])
        # numbers back on with only two figures; move it up one
        new_on = self.layout_form(token, new, stats=("streets", "raised"), move="up|stats")
        self.client.post("/admin/about-layout", data=new_on)
        html = self.client.get("/about").get_data(as_text=True)
        self.assertIn("about-stats__grid--2", html)
        self.assertIn("streets cleaned", html)
        self.assertNotIn("people have chipped in", html)
        self.assertLess(html.find("about-stats__grid"), html.find("Going green"))
        # all numbers off hides the block
        self.client.post("/admin/about-layout", data=self.layout_form(token, new, stats=()))
        self.assertNotIn("about-stats__grid", self.client.get("/about").get_data(as_text=True))

    def test_add_edit_remove_block(self):
        token = self.login()
        r = self.client.post("/admin/about-layout", data={
            "_csrf": token, "action": "add_block", "heading": "Our safety standards",
            "body": "Cones first.\n\nAlways.", "image_side": "right", "shaded": "1",
            "button_text": "Request a clean", "button_url": "/request", "image": (png_bytes(), "s.png")},
            content_type="multipart/form-data")
        self.assertEqual(r.status_code, 302)
        html = self.client.get("/about").get_data(as_text=True)
        self.assertIn("Our safety standards", html)
        self.assertIn("<p>Cones first.</p><p>Always.</p>", html)
        self.assertIn('href="/request">Request a clean', html)
        self.assertIn("custom-block--right", html)
        with self.app.app_context():
            b = dict(query("SELECT * FROM page_blocks", one=True))
        self.assertEqual(self.client.get(f"/static/uploads/{b['image']}").status_code, 200)
        # unsafe link dropped
        self.client.post(f"/admin/about-layout/block/{b['id']}", data={
            "_csrf": token, "heading": "Safety", "body": "x", "button_text": "Go", "button_url": "javascript:alert(1)"})
        html = self.client.get("/about").get_data(as_text=True)
        self.assertNotIn("javascript:", html)
        self.assertIn(">Safety<", html)
        # hide it via layout, then remove it
        from cms.content import ABOUT_BLOCKS
        ids = [k for k, _ in ABOUT_BLOCKS] + [f"block:{b['id']}"]
        self.client.post("/admin/about-layout", data=self.layout_form(token, ids, off=(f"block:{b['id']}",)))
        self.assertNotIn(">Safety<", self.client.get("/about").get_data(as_text=True))
        self.client.post(f"/admin/about-layout/block/{b['id']}", data={"_csrf": token, "action": "delete"})
        with self.app.app_context():
            self.assertIsNone(query("SELECT * FROM page_blocks", one=True))
        self.assertEqual(self.client.get(f"/static/uploads/{b['image']}").status_code, 404)
        self.assertEqual(self.client.get("/about").status_code, 200)
        self.assertEqual(self.client.get(f"/admin/about-layout/block/{b['id']}").status_code, 404)

    def test_empty_block_rejected_and_login_needed(self):
        token = self.login()
        r = self.client.post("/admin/about-layout", data={"_csrf": token, "action": "add_block"}, follow_redirects=True)
        self.assertIn("Give the block a heading", r.get_data(as_text=True))
        self.client.get("/admin/logout")
        self.client.post("/admin/logout", data={"_csrf": token})
        fresh = self.app.test_client()
        self.assertEqual(fresh.get("/admin/about-layout").status_code, 302)
