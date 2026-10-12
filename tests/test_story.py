"""About page photo story managed in admin."""
import re
import unittest

from cms.db import query
from test_flows import Base, png_bytes


class StoryTests(Base):
    def order(self):
        with self.app.app_context():
            return [dict(r) for r in query("SELECT * FROM story_photos ORDER BY sort_order, id")]

    def test_default_story_and_about_page(self):
        rows = self.order()
        self.assertEqual(len(rows), 10)
        self.assertTrue(rows[0]["caption"].startswith("Safety first"))
        page = self.client.get("/about").get_data(as_text=True)
        self.assertIn("How a street gets done", page)
        self.assertIn("Getting to know us", page)
        self.assertIn("Our vision", page)
        self.assertNotIn("R89", page)
        caps = re.findall(r'<span class="story__num">(\d+)</span>([^<]+)', page)
        self.assertEqual(caps[0][0], "01")
        self.assertIn("Safety first", caps[0][1])
        for src in re.findall(r'src="(/static/img/gallery/[^"]+)"', page):
            self.assertEqual(self.client.get(src).status_code, 200, src)
        home = self.client.get("/").get_data(as_text=True)
        self.assertIn('href="/about#story"', home)
        self.assertIn('href="/about"', home)

    def test_admin_adds_reorders_captions_and_removes(self):
        token = self.login()
        self.assertIn("About page photo story", self.client.get("/admin/story").get_data(as_text=True))
        r = self.client.post("/admin/story", data={"_csrf": token, "action": "upload", "where": "start",
                                                   "caption": "Before: the verge we started with",
                                                   "photos": [(png_bytes(), "a.png")]},
                             content_type="multipart/form-data")
        self.assertEqual(r.status_code, 302)
        rows = self.order()
        self.assertEqual(rows[0]["caption"], "Before: the verge we started with")
        self.assertTrue(rows[0]["image"].startswith("story-"))
        self.assertEqual(self.client.get(f"/static/uploads/{rows[0]['image']}").status_code, 200)
        # several at once, inserted before photo 3, in the order chosen
        self.client.post("/admin/story", data={"_csrf": token, "action": "upload", "where": "3",
                                               "photos": [(png_bytes(), "b.png"), (png_bytes(), "c.png")]},
                         content_type="multipart/form-data")
        rows = self.order()
        self.assertEqual(len(rows), 13)
        self.assertTrue(rows[2]["image"].startswith("story-") and rows[3]["image"].startswith("story-"))
        self.assertLess(rows[2]["id"], rows[3]["id"])
        # captions + move down
        first = rows[0]["id"]
        data = {"_csrf": token, "action": "save", "move": f"down:{first}"}
        for i, r in enumerate(rows):
            data[f"caption_{r['id']}"] = f"Note {i + 1}"
            data[f"pos_{r['id']}"] = str(i + 1)
        self.client.post("/admin/story", data=data)
        rows2 = self.order()
        self.assertEqual(rows2[1]["id"], first)
        self.assertEqual(rows2[1]["caption"], "Note 1")
        self.assertEqual(rows2[0]["caption"], "Note 2")
        # jump by position number: last photo to position 1
        last = rows2[-1]["id"]
        data = {"_csrf": token, "action": "save"}
        for i, r in enumerate(rows2):
            data[f"caption_{r['id']}"] = r["caption"]
            data[f"pos_{r['id']}"] = str(i + 1)
        data[f"pos_{last}"] = "0.5"
        self.client.post("/admin/story", data=data)
        self.assertEqual(self.order()[0]["id"], last)
        # notes are escaped on the page
        data = {"_csrf": token, "action": "save", f"caption_{last}": "<script>x</script> Clean", f"pos_{last}": "1"}
        self.client.post("/admin/story", data=data)
        about = self.client.get("/about").get_data(as_text=True)
        self.assertNotIn("<script>x</script>", about)
        # remove
        self.client.post("/admin/story", data={"_csrf": token, "action": "delete", "photo_id": last})
        self.assertEqual(len(self.order()), 12)
        self.assertNotIn(last, [r["id"] for r in self.order()])

    def test_empty_story_hides_section(self):
        token = self.login()
        for r in self.order():
            self.client.post("/admin/story", data={"_csrf": token, "action": "delete", "photo_id": r["id"]})
        page = self.client.get("/about").get_data(as_text=True)
        self.assertNotIn('id="story"', page)
        self.assertIn("No photos yet", self.client.get("/admin/story").get_data(as_text=True))
        # the default story is not re-added on the next start
        with self.app.app_context():
            from cms.db import migrate
            migrate()
        self.assertEqual(self.order(), [])

    def test_story_needs_login_and_csrf(self):
        self.assertEqual(self.client.get("/admin/story").status_code, 302)
        self.login()
        self.assertEqual(self.client.post("/admin/story", data={"action": "save"}).status_code, 400)
