import importlib.util
import io
import json
import os
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError


class FakeHeaders:
    def __init__(self, content_type):
        self.content_type = content_type

    def get_content_type(self):
        return self.content_type


class FakeResponse(io.BytesIO):
    def __init__(self, content, content_type):
        super().__init__(content)
        self.headers = FakeHeaders(content_type)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


class HjemmelagerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        os.environ["HJEMMELAGER_DATA_DIR"] = cls.temp_dir.name
        server_path = Path(__file__).parents[1] / "app" / "server.py"
        spec = importlib.util.spec_from_file_location("hjemmelager_test_server", server_path)
        cls.app = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.app)
        cls.app.init_db()

    @classmethod
    def tearDownClass(cls):
        cls.temp_dir.cleanup()

    def setUp(self):
        with self.app.db() as conn:
            conn.execute("delete from tag_link_sessions")
            conn.execute("delete from events")
            conn.execute("delete from items")
            conn.execute("delete from locations")
            conn.execute("delete from categories")
        self.app.PRODUCT_LOOKUP_CACHE.clear()

    def create_item(self, name, **values):
        data = {
            "name": name,
            "kind": "consumable",
            "quantity": "1",
            "unit": "stk",
            "shopping_enabled": "1",
        }
        data.update(values)
        return self.app.create_item(data)

    def test_nfc_link_touch_conflict_and_cancel(self):
        first = self.create_item("Første")
        second = self.create_item("Andre")

        self.assertEqual(self.app.start_tag_link(first["id"])["status"], "waiting")
        self.assertEqual(self.app.touch_tag("test-tag")["status"], "linked")
        self.assertEqual(self.app.get_item(first["id"])["tag_id"], "test-tag")
        self.assertEqual(self.app.touch_tag("test-tag")["status"], "touched")

        self.app.start_tag_link(second["id"])
        self.assertEqual(self.app.touch_tag("test-tag")["status"], "conflict")
        self.assertIsNone(self.app.get_item(second["id"])["tag_id"])

        self.app.start_tag_link(second["id"])
        self.assertEqual(self.app.cancel_tag_link(second["id"])["status"], "cancelled")
        self.assertEqual(self.app.touch_tag("ukjent-tag")["status"], "not_found")

    def test_shopping_list_uses_target_quantity(self):
        item = self.create_item(
            "Melk",
            quantity="2",
            min_quantity="3",
            target_quantity="10",
        )
        page = self.app.shopping_list_page()
        self.assertIn("Kjøp 8 stk", page)
        self.assertIn("Mål 10", page)

        self.app.set_shopping_enabled(item["id"], False)
        self.assertNotIn("Melk", self.app.shopping_list_page())
        self.assertEqual(self.app.get_item(item["id"])["shopping_enabled"], 0)

    def test_edit_form_can_disable_shopping_list(self):
        item = self.create_item("Kaffe", min_quantity="2")
        updated = self.app.update_item(
            item["id"],
            {"name": "Kaffe", "shopping_enabled": "0"},
        )
        self.assertEqual(updated["shopping_enabled"], 0)

    def test_delete_item_removes_tag_session_and_history(self):
        item = self.create_item("Skal slettes")
        self.app.start_tag_link(item["id"])
        self.assertEqual(self.app.touch_tag("delete-test-tag")["status"], "linked")

        self.assertTrue(self.app.delete_item(item["id"]))
        self.assertIsNone(self.app.get_item(item["id"]))
        with self.app.db() as conn:
            event_count = conn.execute(
                "select count(*) as total from events where item_id = ?", (item["id"],)
            ).fetchone()["total"]
            session_count = conn.execute(
                "select count(*) as total from tag_link_sessions where item_id = ?",
                (item["id"],),
            ).fetchone()["total"]
        self.assertEqual(event_count, 0)
        self.assertEqual(session_count, 0)

    def test_expiry_flags(self):
        expired = self.create_item(
            "Gammel",
            best_before=(date.today() - timedelta(days=1)).isoformat(),
        )
        soon = self.create_item(
            "Snart",
            best_before=(date.today() + timedelta(days=7)).isoformat(),
        )
        later = self.create_item(
            "Senere",
            best_before=(date.today() + timedelta(days=30)).isoformat(),
        )

        self.assertTrue(expired["is_expired"])
        self.assertTrue(soon["expires_soon"])
        self.assertFalse(later["expires_soon"])

    def test_product_lookup_fills_name_brand_and_local_image(self):
        payload = json.dumps(
            {
                "status": "success",
                "product": {
                    "code": "1234567890123",
                    "product_name_no": "Testpålegg",
                    "brands": "Testmerket",
                    "quantity": "250 g",
                    "image_front_small_url": (
                        "https://images.openfoodfacts.org/images/products/test.200.jpg"
                    ),
                },
            }
        ).encode()

        def fake_urlopen(request, timeout):
            if "api/v3.6/product" in request.full_url:
                return FakeResponse(payload, "application/json")
            return FakeResponse(b"fake-jpeg", "image/jpeg")

        with mock.patch.object(self.app, "urlopen", side_effect=fake_urlopen):
            product = self.app.lookup_product("1234567890123")

        self.assertEqual(product["status"], "found")
        self.assertEqual(product["name"], "Testpålegg")
        self.assertEqual(product["brand"], "Testmerket")
        self.assertTrue(product["image_data"].startswith("data:image/jpeg;base64,"))

    def test_product_lookup_has_manual_fallback(self):
        error = HTTPError(
            "https://world.openfoodfacts.org/api/v3.6/product/1234567890123.json",
            404,
            "Not Found",
            {},
            None,
        )
        with mock.patch.object(self.app, "urlopen", side_effect=error):
            product = self.app.lookup_product("1234567890123")

        self.assertEqual(product["status"], "not_found")
        self.assertIn("manuelt", product["message"])


if __name__ == "__main__":
    unittest.main()
