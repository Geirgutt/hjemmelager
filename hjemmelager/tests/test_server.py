import importlib.util
import io
import json
import os
import tempfile
import unittest
from copy import deepcopy
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

    def test_shopping_list_groups_remaining_items_by_category(self):
        self.create_item(
            "Melk",
            quantity="0",
            min_quantity="1",
            category="Meieri",
        )
        self.create_item(
            "Såpe",
            quantity="0",
            min_quantity="1",
            category="Husholdning",
        )

        content = self.app.shopping_list_page()

        self.assertIn("Meieri", content)
        self.assertIn("Husholdning", content)
        self.assertIn('class="shopping-groups"', content)

    def test_search_tolerates_small_typing_errors(self):
        item = self.create_item(
            "Havregryn",
            category="Matvarer",
            location="Kjøkkenskap",
        )

        self.assertTrue(self.app.item_matches_search(item, "havregrn"))
        self.assertTrue(self.app.item_matches_search(item, "kjokkenskap"))
        self.assertFalse(self.app.item_matches_search(item, "slagdrill"))

    def test_last_quantity_adjustment_can_be_undone_once(self):
        item = self.create_item("Kaffe", quantity="3")
        self.app.adjust_item(item["id"], -1, "web")

        result = self.app.undo_last_adjustment(item["id"])
        second_attempt = self.app.undo_last_adjustment(item["id"])

        self.assertEqual(result["status"], "undone")
        self.assertEqual(result["item"]["quantity"], 3)
        self.assertEqual(second_attempt["status"], "unavailable")
        self.assertIn("Angre siste endring", self.app.adjustment_notice(item))

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

    def test_backup_contains_inventory_and_history(self):
        item = self.create_item(
            "Backupvare",
            location="Bod",
            category="Test",
            image_url="data:image/jpeg;base64,ZmFrZQ==",
        )
        self.app.adjust_item(item["id"], 2, "backup-test")

        backup = self.app.create_backup_payload()
        self.assertEqual(backup["format"], "hjemmelager-backup")
        self.assertEqual(backup["format_version"], 1)
        self.assertEqual(backup["data"]["items"][0]["name"], "Backupvare")
        self.assertTrue(backup["data"]["items"][0]["image_url"].startswith("data:image/"))
        self.assertEqual(backup["data"]["locations"][0]["name"], "Bod")
        self.assertEqual(backup["data"]["categories"][0]["name"], "Test")
        self.assertGreaterEqual(len(backup["data"]["events"]), 2)

    def test_restore_replaces_data_and_keeps_before_copy(self):
        original = self.create_item("Original", quantity="3", location="Bod")
        backup = self.app.create_backup_payload()
        self.app.delete_item(original["id"])
        self.create_item("Midlertidig")

        result = self.app.restore_backup_payload(backup)
        restored = self.app.list_items()
        self.assertEqual([item["name"] for item in restored], ["Original"])
        self.assertEqual(restored[0]["quantity"], 3)
        before_path = Path(self.temp_dir.name) / result["before_filename"]
        self.assertTrue(before_path.is_file())
        before_payload = json.loads(before_path.read_text(encoding="utf-8"))
        self.assertEqual(before_payload["data"]["items"][0]["name"], "Midlertidig")

    def test_invalid_restore_rolls_back_without_data_loss(self):
        current = self.create_item("Behold meg")
        invalid = self.app.create_backup_payload()
        duplicate = deepcopy(invalid["data"]["items"][0])
        duplicate["id"] = current["id"] + 1
        duplicate["name"] = "Duplikat"
        duplicate["tag_id"] = "samme-tag"
        invalid["data"]["items"][0]["tag_id"] = "samme-tag"
        invalid["data"]["items"].append(duplicate)

        with self.assertRaises(self.app.sqlite3.IntegrityError):
            self.app.restore_backup_payload(invalid)

        self.assertEqual([item["name"] for item in self.app.list_items()], ["Behold meg"])

    def test_rejects_unknown_backup_format(self):
        with self.assertRaisesRegex(ValueError, "ukjent"):
            self.app.parse_backup_bytes(b'{"format": "noe-annet", "format_version": 1}')

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

    def test_expiry_filter_includes_expired_and_sorts_nearest_first(self):
        later = self.create_item(
            "Senere",
            best_before=(date.today() + timedelta(days=20)).isoformat(),
        )
        soon = self.create_item(
            "Snart",
            best_before=(date.today() + timedelta(days=7)).isoformat(),
        )
        expired = self.create_item(
            "Utløpt",
            best_before=(date.today() - timedelta(days=1)).isoformat(),
        )
        self.create_item("Uten dato")

        where, params = self.app.build_item_filters(
            kind="consumable",
            expiry_only=True,
        )
        filtered = self.app.list_items(where, params, sort="best_before")

        self.assertEqual(
            [item["id"] for item in filtered],
            [expired["id"], soon["id"]],
        )
        self.assertNotIn(later["id"], [item["id"] for item in filtered])

    def test_empty_states_offer_a_clear_next_step(self):
        consumable = self.app.inventory_empty_state("consumable")
        thing = self.app.inventory_empty_state("thing")
        filtered = self.app.inventory_empty_state(
            "consumable",
            filtered=True,
            clear_url=".?kind=consumable",
        )

        self.assertIn("Skann strekkode", consumable)
        self.assertIn("new?kind=thing", thing)
        self.assertIn("Ingen treff", filtered)
        self.assertIn("Vis hele lageret", filtered)

    def test_new_thing_form_uses_plain_thing_language(self):
        form = self.app.item_form(kind="thing")

        self.assertIn("Hva heter gjenstanden?", form)
        self.assertIn("For eksempel Slagdrill", form)
        self.assertIn("Lagre gjenstand", form)
        self.assertIn('type="hidden" name="kind" value="thing"', form)
        self.assertNotIn("Skann strekkode", form)
        self.assertIn('<details class="card form-section" hidden>', form)

    def test_new_item_start_page_offers_three_clear_paths(self):
        content = self.app.new_item_start_page()

        self.assertIn("Skann en vare", content)
        self.assertIn("Skriv inn en vare", content)
        self.assertIn("Legg inn en gjenstand", content)
        self.assertIn('href="scan"', content)
        self.assertIn('href="new?kind=consumable"', content)
        self.assertIn('href="new?kind=thing"', content)

    def test_new_form_keeps_type_and_location_out_of_main_fields(self):
        content = self.app.item_form(kind="consumable")

        self.assertIn('type="hidden" name="kind" value="consumable"', content)
        self.assertNotIn('<select name="kind" id="item-kind">', content)
        self.assertEqual(content.count('<select name="location">'), 1)
        self.assertIn("Legg til ny plassering", content)

    def test_product_suggestion_explains_what_was_filled(self):
        content = self.app.item_form(barcode="1234567890123")

        self.assertIn('const filled = ["navn", "enhet", "kategori"]', content)
        self.assertIn('filled.push("bilde")', content)
        self.assertIn("Kontroller og lagre", content)

    def test_image_picker_does_not_force_camera(self):
        form = self.app.item_form()

        self.assertIn("Velg eller ta bilde", form)
        self.assertIn("Valgfritt – velg fra telefonen eller bruk kameraet", form)
        self.assertNotIn('capture="environment"', form)
        self.assertIn('accept="image/*"', form)
        self.assertIn("Store bilder gjøres mindre automatisk", form)

    def test_new_item_can_continue_directly_to_nfc_linking(self):
        item = self.create_item("NFC etter lagring")
        redirect = self.app.new_item_redirect(
            item,
            {"link_nfc_after_save": "1"},
        )

        self.assertEqual(redirect, f"item/{item['id']}/tag-link")
        session = self.app.get_tag_link_session(item["id"])
        self.assertEqual(session["status"], "waiting")

    def test_new_item_redirects_to_clear_created_confirmation(self):
        item = self.create_item("Ny bekreftelse")

        redirect = self.app.new_item_redirect(item, {})
        notice = self.app.created_item_notice(item)

        self.assertEqual(redirect, f"item/{item['id']}?created=1")
        self.assertIn("Varen er lagt til", notice)
        self.assertIn("Koble NFC-tag", notice)
        self.assertIn("Legg til detaljer", notice)
        self.assertIn("Legg til en ny", notice)

    def test_multipart_image_is_saved_on_new_item(self):
        boundary = "hjemmelager-test-boundary"
        image_bytes = b"\xff\xd8fake-jpeg\xff\xd9"
        raw = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="name"\r\n\r\n'
            "Bildeprodukt\r\n"
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="image_file"; filename="produkt.jpg"\r\n'
            "Content-Type: image/jpeg\r\n\r\n"
        ).encode() + image_bytes + f"\r\n--{boundary}--\r\n".encode()

        data = self.app.parse_multipart_form(
            raw,
            f"multipart/form-data; boundary={boundary}",
        )
        item = self.app.create_item(data)

        self.assertEqual(item["name"], "Bildeprodukt")
        self.assertTrue(item["image_url"].startswith("data:image/jpeg;base64,"))

    def test_oversized_processed_image_is_rejected(self):
        oversized = (
            "data:image/jpeg;base64,"
            + "A" * ((self.app.MAX_STORED_IMAGE_BYTES * 4 // 3) + 16)
        )

        with self.assertRaisesRegex(ValueError, "fortsatt for stort"):
            self.app.image_value({"image_file_data_url": oversized})

    def test_alerts_combine_low_stock_and_expiry_without_double_counting(self):
        self.create_item(
            "Melk",
            quantity="1",
            min_quantity="2",
            target_quantity="5",
            best_before=(date.today() + timedelta(days=2)).isoformat(),
        )
        self.create_item(
            "Gammel ost",
            quantity="3",
            min_quantity="0",
            best_before=(date.today() - timedelta(days=1)).isoformat(),
        )

        alerts = self.app.create_alerts_payload(days=14)

        self.assertEqual(alerts["summary"]["total"], 2)
        self.assertEqual(alerts["summary"]["low_stock"], 1)
        self.assertEqual(alerts["summary"]["best_before"], 2)
        self.assertEqual(alerts["summary"]["expired"], 1)
        self.assertEqual(alerts["low_stock"][0]["buy_quantity"], 4)
        self.assertIn("Må kjøpes: Melk (4 stk)", alerts["message"])
        self.assertIn("Gammel ost (utløpt)", alerts["message"])

    def test_alert_days_are_bounded(self):
        self.assertEqual(self.app.create_alerts_payload(0)["days_ahead"], 1)
        self.assertEqual(self.app.create_alerts_payload(999)["days_ahead"], 90)
        self.assertEqual(self.app.create_alerts_payload("feil")["days_ahead"], 14)

    def test_alert_suggests_one_when_stock_equals_minimum(self):
        self.create_item("Havregryn", quantity="2", min_quantity="2")

        alerts = self.app.create_alerts_payload()

        self.assertEqual(alerts["low_stock"][0]["buy_quantity"], 1)
        self.assertIn("Havregryn (1 stk)", alerts["message"])

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

    def test_home_assistant_tag_event_links_waiting_item(self):
        item = self.create_item("Kontortagg")
        self.app.start_tag_link(item["id"])

        result = self.app.handle_home_assistant_event(
            {
                "type": "event",
                "event": {
                    "event_type": "tag_scanned",
                    "data": {"tag_id": "office-tag-01"},
                },
            }
        )

        self.assertEqual(result["status"], "linked")
        self.assertEqual(self.app.get_item(item["id"])["tag_id"], "office-tag-01")
        self.assertEqual(
            self.app.get_tag_link_session(item["id"])["status"],
            "linked",
        )

    def test_home_assistant_listener_ignores_other_events(self):
        result = self.app.handle_home_assistant_event(
            {
                "type": "event",
                "event": {
                    "event_type": "state_changed",
                    "data": {"tag_id": "must-not-link"},
                },
            }
        )

        self.assertIsNone(result)

    def test_home_assistant_nfc_state_is_visible_on_link_page(self):
        item = self.create_item("Statusvare")
        session = self.app.start_tag_link(item["id"])
        self.app.set_home_assistant_nfc_state(
            "connected",
            "Klar til å motta NFC-skanningen.",
        )

        content = self.app.tag_link_page(item, session)

        self.assertIn('data-state="connected"', content)
        self.assertIn("Klar til å motta NFC-skanningen.", content)

    def test_dashboard_summary_combines_inventory_alerts_and_activity(self):
        self.create_item(
            "Melk",
            quantity="0",
            min_quantity="1",
            best_before=date.today().isoformat(),
        )

        summary = self.app.dashboard_summary()

        self.assertEqual(summary["total"], 1)
        self.assertEqual(summary["low_stock"], 1)
        self.assertEqual(summary["best_before"], 1)
        self.assertEqual(summary["recent"]["item_name"], "Melk")

    def test_inventory_csv_is_readable_and_keeps_norwegian_text(self):
        self.create_item(
            "Havregryn",
            quantity="2",
            category="Tørrvarer",
            location="Kjøkken",
        )

        content = self.app.inventory_csv_bytes().decode("utf-8-sig")

        self.assertIn("Navn;Type;Antall;Enhet", content)
        self.assertIn("Havregryn;Forbruksvare;2;stk", content)
        self.assertIn("Tørrvarer;Kjøkken", content)

    def test_activity_page_explains_recent_change(self):
        item = self.create_item("Batterier")
        self.app.adjust_item(item["id"], 2)

        content = self.app.activity_page()

        self.assertIn("Batterier: lager endret (+2)", content)
        self.assertIn("Historikk", content)


if __name__ == "__main__":
    unittest.main()
