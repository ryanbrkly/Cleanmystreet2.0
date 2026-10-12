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

    def test_needs_login_and_known_page(self):
        self.assertEqual(self.client.get("/admin/content/about").status_code, 302)
        self.login()
        self.assertEqual(self.client.get("/admin/content/nope").status_code, 404)
