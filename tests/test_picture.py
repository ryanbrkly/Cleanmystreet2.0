"""Custom project picture."""
import re
import unittest

from cms.db import query
from test_flows import Base, png_bytes


class PictureTests(Base):
    def project(self, pid):
        with self.app.app_context():
            return dict(query("SELECT * FROM projects WHERE id=?", (pid,), one=True))

    def test_upload_choose_position_remove(self):
        self.submit_request()
        p, token = self.approve()
        # defaults to the requester's photo
        first = self.project(p["id"])["cover_image"]
        self.assertTrue(first.startswith("request-"))
        page = self.client.get(f"/admin/projects/{p['id']}").get_data(as_text=True)
        self.assertIn("Project picture", page)
        # upload a new one
        self.client.post(f"/admin/projects/{p['id']}", data={"_csrf": token, "action": "set_cover",
                                                             "cover": (png_bytes(), "pic.png")},
                         content_type="multipart/form-data")
        cover = self.project(p["id"])["cover_image"]
        self.assertTrue(cover.startswith("cover-"))
        self.assertEqual(self.client.get(f"/static/uploads/{first}").status_code, 200)  # request photo kept
        home = self.client.get("/projects").get_data(as_text=True)
        self.assertIn(f"/static/uploads/{cover}", home)
        self.assertIn(f"/static/uploads/{cover}", self.client.get(f"/projects/{p['slug']}").get_data(as_text=True))
        # position
        self.client.post(f"/admin/projects/{p['id']}", data={"_csrf": token, "action": "cover_position", "cover_position": "top"})
        self.assertEqual(self.project(p["id"])["cover_position"], "top")
        self.assertIn("object-position:50% 0%", self.client.get(f"/projects/{p['slug']}").get_data(as_text=True))
        self.client.post(f"/admin/projects/{p['id']}", data={"_csrf": token, "action": "cover_position", "cover_position": "evil"})
        self.assertEqual(self.project(p["id"])["cover_position"], "top")
        # pick a progress photo instead; the replaced upload is deleted
        self.client.post(f"/admin/projects/{p['id']}", data={"_csrf": token, "action": "add_photos", "phase": "after",
                                                             "photos": (png_bytes(), "after.png")},
                         content_type="multipart/form-data")
        with self.app.app_context():
            upd = query("SELECT image FROM updates WHERE project_id=?", (p["id"],), one=True)["image"]
        self.client.post(f"/admin/projects/{p['id']}", data={"_csrf": token, "action": "use_photo", "photo": upd})
        self.assertEqual(self.project(p["id"])["cover_image"], upd)
        self.assertEqual(self.client.get(f"/static/uploads/{cover}").status_code, 404)
        # can't point at some other file
        self.client.post(f"/admin/projects/{p['id']}", data={"_csrf": token, "action": "use_photo", "photo": "../../secret"})
        self.assertEqual(self.project(p["id"])["cover_image"], upd)
        # remove → standard photo, and the progress photo itself stays
        self.client.post(f"/admin/projects/{p['id']}", data={"_csrf": token, "action": "remove_cover"})
        self.assertIsNone(self.project(p["id"])["cover_image"])
        self.assertEqual(self.client.get(f"/static/uploads/{upd}").status_code, 200)
        self.assertIn("crew-site-sm.jpg", self.client.get("/projects").get_data(as_text=True))
        self.assertEqual(self.client.get(f"/projects/{p['slug']}").status_code, 200)

    def test_picture_when_approving(self):
        self.submit_request()
        token = self.login()
        with self.app.app_context():
            rid = query("SELECT id FROM requests", one=True)["id"]
        self.client.post(f"/admin/requests/{rid}", data={"_csrf": token, "action": "save_quote",
                                                        "line_items": "Verges | 1000", "scope": "x"})
        self.client.post(f"/admin/requests/{rid}", data={"_csrf": token, "action": "approve", "service_type": "once",
                                                        "goal_amount": "1000", "min_contribution": "100",
                                                        "funding_days": "14", "cover": (png_bytes(), "c.png")},
                         content_type="multipart/form-data")
        with self.app.app_context():
            self.assertTrue(query("SELECT cover_image FROM projects", one=True)["cover_image"].startswith("cover-"))

    def test_bad_file_rejected(self):
        self.submit_request()
        p, token = self.approve()
        before = self.project(p["id"])["cover_image"]
        import io
        r = self.client.post(f"/admin/projects/{p['id']}", data={"_csrf": token, "action": "set_cover",
                                                                 "cover": (io.BytesIO(b"not an image"), "x.jpg")},
                             content_type="multipart/form-data", follow_redirects=True)
        self.assertEqual(self.project(p["id"])["cover_image"], before)
        self.assertIn("flash--error", r.get_data(as_text=True))
