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
            conn.execute("delete from location_tag_link_sessions")
            conn.execute("delete from tag_link_sessions")
            conn.execute("delete from deleted_items")
            conn.execute("delete from events")
            conn.execute("delete from items")
            conn.execute("delete from location_tags")
            conn.execute("delete from locations")
            conn.execute("delete from categories")
        self.app.PRODUCT_LOOKUP_CACHE.clear()
        self.app.HOME_ASSISTANT_ALERT_EVENT.clear()
        self.app.set_home_assistant_alert_state(
            "starting",
            "Oppretter varselsensor i Home Assistant …",
        )

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

    def test_location_nfc_opens_a_filtered_location_without_replacing_item_tags(self):
        item = self.create_item("Melk", location="Kjøleskap > Dør")
        self.assertEqual(
            self.app.start_location_tag_link("Kjøleskap > Dør")["status"],
            "waiting",
        )

        linked = self.app.touch_tag("fridge-door-tag")
        touched = self.app.touch_tag("fridge-door-tag")

        self.assertEqual(linked["status"], "linked")
        self.assertEqual(linked["location"], "Kjøleskap > Dør")
        self.assertEqual(touched["status"], "touched")
        self.assertEqual(touched["location"], "Kjøleskap > Dør")
        self.assertIsNone(self.app.get_item(item["id"])["tag_id"])
        self.assertEqual(
            self.app.get_location_tag("Kjøleskap > Dør")["tag_id"],
            "fridge-door-tag",
        )

    def test_same_nfc_tag_cannot_be_linked_to_item_and_location(self):
        item = self.create_item("Kaffe", location="Matskap")
        self.app.start_tag_link(item["id"])
        self.app.touch_tag("shared-tag")

        self.app.start_location_tag_link("Matskap")
        result = self.app.touch_tag("shared-tag")

        self.assertEqual(result["status"], "conflict")
        self.assertIsNone(self.app.get_location_tag("Matskap"))

    def test_location_nfc_pages_explain_filtered_opening(self):
        self.create_item("Pasta", location="Matskap")
        session = self.app.start_location_tag_link("Matskap")
        link_page = self.app.location_tag_link_page("Matskap", session)
        self.app.touch_tag("pantry-tag")
        setup_page = self.app.location_tag_open_setup_page(
            "Matskap", addon_slug="abc_hjemmelager"
        )
        organize = self.app.organize_page()

        self.assertIn("plasseringen «Matskap»", link_page)
        self.assertIn("api/location-tag-link/status", link_page)
        self.assertIn("ferdig filtrert", setup_page)
        self.assertIn("Vis varer", organize)
        self.assertIn("Direkte åpning", organize)

    def test_quick_adjustment_stays_in_inventory_and_has_color_feedback(self):
        item = self.create_item("Melk", quantity="3")
        card = self.app.item_card(item)
        row = self.app.item_row(item)
        full_page = self.app.page("Varer", card)

        self.assertEqual(card.count('class="quick-adjust"'), 2)
        self.assertNotIn('class="quick-adjust"', row)
        self.assertIn(f'<a class="item-row" href="item/{item["id"]}">', row)
        self.assertNotIn("Åpne</button>", row)
        self.assertIn("data-quantity-display", card)
        self.assertIn("handleQuickAdjustment", full_page)
        self.assertIn("quantity-increased", full_page)
        self.assertIn("quantity-decreased", full_page)
        self.assertIn("quickAdjustmentSeries", full_page)
        self.assertIn(": fra ", full_page)
        self.assertIn("quickAdjustmentSeries.delete(itemId)", full_page)
        self.assertIn("event.preventDefault()", full_page)

    def test_direct_nfc_links_open_hjemmelager_panel_with_tag(self):
        links = self.app.direct_nfc_links(
            "tag med mellomrom/æ",
            "abc123_hjemmelager",
        )

        self.assertEqual(
            links["android"],
            "homeassistant://navigate/hassio/ingress/abc123_hjemmelager"
            "?server=default#hjemmelager-tag=tag%20med%20mellomrom%2F%C3%A6",
        )
        self.assertTrue(
            links["iphone"].startswith(
                "https://www.home-assistant.io/ios/nfc/?url="
            )
        )
        self.assertIn("%3Fserver%3Ddefault%23hjemmelager-tag%3D", links["iphone"])

    def test_direct_nfc_setup_keeps_linked_tag_and_offers_both_platforms(self):
        item = self.create_item("Direktevare", tag_id="direct-tag-01")

        content = self.app.tag_open_setup_page(item, "local_hjemmelager")

        self.assertIn("Åpne «Direktevare» fra NFC", content)
        self.assertIn("Skriv taggen", content)
        self.assertIn("iPhone", content)
        self.assertIn("Test i Home Assistant", content)
        self.assertIn("skal ikke åpnes i nettleseren", content)
        self.assertIn("window.top.location.pathname", content)
        self.assertIn('document.getElementById("copy-iphone-url").dataset.copyUrl', content)
        self.assertIn("Direktelenken bruker Home Assistant-stien", content)
        self.assertIn("homeassistant://navigate/hassio/ingress/local_hjemmelager", content)
        self.assertEqual(self.app.get_item(item["id"])["tag_id"], "direct-tag-01")

    def test_common_page_handles_direct_nfc_link(self):
        content = self.app.page("Varer", "<h1>Test</h1>", "/ingress")

        self.assertIn('queryValues.get("hjemmelager_tag")', content)
        self.assertIn('fragmentValues.get("hjemmelager-tag")', content)
        self.assertIn('window.location.replace("tag/open?tag_id="', content)

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

    def test_deleted_item_can_be_restored_with_tag_and_history(self):
        item = self.create_item("Skal slettes")
        self.app.start_tag_link(item["id"])
        self.assertEqual(self.app.touch_tag("delete-test-tag")["status"], "linked")

        deletion_id = self.app.delete_item(item["id"])
        self.assertTrue(deletion_id)
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

        result = self.app.restore_deleted_item(deletion_id)
        restored = result["item"]
        self.assertEqual(result["status"], "restored")
        self.assertEqual(restored["id"], item["id"])
        self.assertEqual(restored["tag_id"], "delete-test-tag")
        with self.app.db() as conn:
            restored_events = conn.execute(
                "select count(*) as total from events where item_id = ?",
                (item["id"],),
            ).fetchone()["total"]
        self.assertGreaterEqual(restored_events, 3)

    def test_backup_contains_inventory_and_history(self):
        item = self.create_item(
            "Backupvare",
            location="Bod",
            category="Test",
            image_url="data:image/jpeg;base64,ZmFrZQ==",
            nutrition_energy_kcal_100g="245",
            nutrition_proteins_100g="8.5",
            nutrition_serving_size="30",
            nutrition_serving_unit="g",
        )
        self.app.adjust_item(item["id"], 2, "backup-test")
        self.app.start_location_tag_link("Bod")
        self.app.touch_tag("storage-tag")

        backup = self.app.create_backup_payload()
        self.assertEqual(backup["format"], "hjemmelager-backup")
        self.assertEqual(backup["format_version"], 1)
        self.assertEqual(backup["data"]["items"][0]["name"], "Backupvare")
        self.assertTrue(backup["data"]["items"][0]["image_url"].startswith("data:image/"))
        saved_nutrition = json.loads(backup["data"]["items"][0]["nutrition_json"])
        self.assertEqual(saved_nutrition["energy_kcal_100g"], 245)
        self.assertEqual(saved_nutrition["serving_unit"], "g")
        self.assertEqual(backup["data"]["locations"][0]["name"], "Bod")
        self.assertEqual(backup["data"]["location_tags"][0]["tag_id"], "storage-tag")
        self.assertEqual(backup["data"]["categories"][0]["name"], "Test")
        self.assertGreaterEqual(len(backup["data"]["events"]), 2)

    def test_restore_replaces_data_and_keeps_before_copy(self):
        original = self.create_item("Original", quantity="3", location="Bod")
        self.app.start_location_tag_link("Bod")
        self.app.touch_tag("restore-location-tag")
        backup = self.app.create_backup_payload()
        self.app.delete_item(original["id"])
        self.create_item("Midlertidig")

        result = self.app.restore_backup_payload(backup)
        restored = self.app.list_items()
        self.assertEqual([item["name"] for item in restored], ["Original"])
        self.assertEqual(restored[0]["quantity"], 3)
        self.assertEqual(
            self.app.get_location_tag("Bod")["tag_id"],
            "restore-location-tag",
        )
        before_path = Path(self.temp_dir.name) / result["before_filename"]
        self.assertTrue(before_path.is_file())
        before_payload = json.loads(before_path.read_text(encoding="utf-8"))
        self.assertEqual(before_payload["data"]["items"][0]["name"], "Midlertidig")

    def test_backup_restore_keeps_expiry_batches(self):
        first = (date.today() + timedelta(days=3)).isoformat()
        second = (date.today() + timedelta(days=9)).isoformat()
        item = self.create_item("Melk", quantity="2", best_before=first)
        self.app.add_expiry_batch(item["id"], 3, second)
        backup = self.app.create_backup_payload()

        self.app.delete_item(item["id"])
        self.app.restore_backup_payload(backup)
        restored = self.app.get_item(item["id"])

        self.assertEqual(restored["quantity"], 5)
        self.assertEqual(
            restored["expiry_batches"],
            [
                {"best_before": first, "quantity": 2},
                {"best_before": second, "quantity": 3},
            ],
        )

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

    def test_expiry_batches_keep_separate_dates_and_use_oldest_first(self):
        later = (date.today() + timedelta(days=10)).isoformat()
        sooner = (date.today() + timedelta(days=3)).isoformat()
        item = self.create_item("Melk", quantity="2", best_before=later)

        item = self.app.add_expiry_batch(item["id"], 3, sooner)

        self.assertEqual(item["quantity"], 5)
        self.assertEqual(
            item["expiry_batches"],
            [
                {"best_before": sooner, "quantity": 3},
                {"best_before": later, "quantity": 2},
            ],
        )
        self.assertEqual(item["best_before"], sooner)

        item = self.app.adjust_item(item["id"], -4, "test")

        self.assertEqual(item["quantity"], 1)
        self.assertEqual(
            item["expiry_batches"],
            [{"best_before": later, "quantity": 1}],
        )
        self.assertEqual(item["best_before"], later)

    def test_removing_expiry_date_keeps_quantity(self):
        best_before = (date.today() + timedelta(days=5)).isoformat()
        item = self.create_item("Yoghurt", quantity="4", best_before=best_before)

        item = self.app.clear_expiry_batch_date(item["id"], best_before)

        self.assertEqual(item["quantity"], 4)
        self.assertEqual(item["expiry_batches"], [])
        self.assertEqual(item["undated_quantity"], 4)
        self.assertEqual(item["best_before"], "")

    def test_existing_undated_stock_can_be_distributed_across_dates(self):
        first = (date.today() + timedelta(days=4)).isoformat()
        second = (date.today() + timedelta(days=9)).isoformat()
        item = self.create_item("Melk", quantity="10")

        self.app.add_expiry_batch(
            item["id"], 6, first, from_existing=True
        )
        item = self.app.add_expiry_batch(
            item["id"], 4, second, from_existing=True
        )

        self.assertEqual(item["quantity"], 10)
        self.assertEqual(item["undated_quantity"], 0)
        self.assertEqual(
            item["expiry_batches"],
            [
                {"best_before": first, "quantity": 6},
                {"best_before": second, "quantity": 4},
            ],
        )
        with self.assertRaisesRegex(ValueError, "mangler dato"):
            self.app.add_expiry_batch(
                item["id"], 1, second, from_existing=True
            )

    def test_editing_item_preserves_multiple_expiry_batches(self):
        first = (date.today() + timedelta(days=2)).isoformat()
        second = (date.today() + timedelta(days=8)).isoformat()
        item = self.create_item("Fløte", quantity="2", best_before=first)
        self.app.add_expiry_batch(item["id"], 3, second)

        updated = self.app.update_item(
            item["id"],
            {
                "name": "Kremfløte",
                "quantity": "5",
                "kind": "consumable",
                "unit": "stk",
                "shopping_enabled": "1",
            },
        )

        self.assertEqual(updated["name"], "Kremfløte")
        self.assertEqual(len(updated["expiry_batches"]), 2)
        self.assertEqual(updated["best_before"], first)

    def test_undo_restores_consumed_expiry_batch(self):
        best_before = (date.today() + timedelta(days=4)).isoformat()
        item = self.create_item("Kefir", quantity="3", best_before=best_before)
        self.app.adjust_item(item["id"], -2, "test")

        result = self.app.undo_last_adjustment(item["id"])

        self.assertEqual(result["item"]["quantity"], 3)
        self.assertEqual(
            result["item"]["expiry_batches"],
            [{"best_before": best_before, "quantity": 3}],
        )

    def test_expiry_panel_explains_batches_and_date_removal(self):
        best_before = (date.today() + timedelta(days=6)).isoformat()
        item = self.create_item("Rømme", quantity="2", best_before=best_before)

        content = self.app.expiry_batches_panel(item)

        self.assertIn("Holdbarhetspartier", content)
        self.assertIn("Legg til parti", content)
        self.assertIn("Fjern dato", content)
        self.assertIn("Finnes allerede i totalen", content)

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

    def test_nutrition_form_is_collapsed_with_manual_and_refresh_controls(self):
        content = self.app.item_form(barcode="1234567890123")

        self.assertIn("Næringsinnhold", content)
        self.assertIn('name="nutrition_energy_kcal_100g"', content)
        self.assertIn('name="nutrition_energy_kcal_serving"', content)
        self.assertIn('name="nutrition_carbohydrates_100g"', content)
        self.assertIn('id="nutrition-refresh"', content)
        self.assertIn("Registrer eller rediger hos Open Food Facts", content)
        self.assertIn('(forceRefresh ? "&refresh=1" : "")', content)

    def test_nutrition_is_stored_locally_and_preserved_on_partial_update(self):
        item = self.create_item(
            "Havregryn",
            nutrition_energy_kcal_100g="370",
            nutrition_fat_100g="7",
            nutrition_proteins_100g="13,2",
            nutrition_serving_size="40",
            nutrition_serving_unit="g",
        )

        self.assertEqual(item["nutrition"]["energy_kcal_100g"], 370)
        self.assertEqual(item["nutrition"]["proteins_100g"], 13.2)
        updated = self.app.update_item(
            item["id"],
            {
                "name": "Havregryn fin",
                "kind": "consumable",
                "quantity": "1",
                "unit": "stk",
                "shopping_enabled": "1",
            },
        )
        self.assertEqual(updated["nutrition"], item["nutrition"])

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

    def test_item_form_has_top_save_and_unsaved_changes_dialog(self):
        content = self.app.item_form(kind="consumable")

        self.assertIn('id="item-form-top-save"', content)
        self.assertIn('id="item-form-return-to"', content)
        self.assertIn("Du har ulagrede endringer", content)
        self.assertIn('id="unsaved-save"', content)
        self.assertIn('id="unsaved-discard"', content)
        self.assertIn('id="unsaved-stay"', content)
        self.assertIn('window.addEventListener("popstate"', content)
        self.assertIn('window.addEventListener("beforeunload"', content)

    def test_form_can_save_before_safe_internal_navigation(self):
        item = self.create_item("Navigasjonsvare")

        self.assertEqual(
            self.app.new_item_redirect(item, {"return_to": "organize"}),
            "organize",
        )
        self.assertEqual(
            self.app.safe_form_return_target(".?kind=all"),
            ".?kind=all",
        )
        self.assertEqual(
            self.app.safe_form_return_target("item/12?changed=1"),
            "item/12?changed=1",
        )
        self.assertEqual(self.app.safe_form_return_target("../organize"), "")
        self.assertEqual(self.app.safe_form_return_target("%2e%2e/organize"), "")
        self.assertEqual(self.app.safe_form_return_target("item%5c..%5corganize"), "")
        self.assertEqual(self.app.safe_form_return_target("/organize"), "")
        self.assertEqual(
            self.app.safe_form_return_target("https://example.com/"),
            "",
        )

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

    def test_alert_sensor_is_published_through_home_assistant_api(self):
        self.create_item("Melk", quantity="1", min_quantity="2")
        self.app.HOME_ASSISTANT_ALERT_EVENT.clear()

        with mock.patch.dict(os.environ, {"SUPERVISOR_TOKEN": "test-token"}):
            with mock.patch.object(
                self.app,
                "urlopen",
                return_value=FakeResponse(b"{}", "application/json"),
            ) as mocked_urlopen:
                published = self.app.publish_home_assistant_alerts()

        request = mocked_urlopen.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertTrue(published)
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(
            request.full_url,
            "http://supervisor/core/api/states/sensor.hjemmelager_varsler",
        )
        self.assertEqual(request.get_header("Authorization"), "Bearer test-token")
        self.assertEqual(payload["state"], "1")
        self.assertEqual(payload["attributes"]["low_stock"], 1)
        self.assertIn("Må kjøpes: Melk", payload["attributes"]["message"])
        self.assertEqual(
            self.app.get_home_assistant_alert_state()["status"], "connected"
        )

    def test_alert_sensor_waits_for_home_assistant_in_local_preview(self):
        with mock.patch.dict(os.environ, {"SUPERVISOR_TOKEN": ""}):
            with mock.patch.object(self.app, "urlopen") as mocked_urlopen:
                published = self.app.publish_home_assistant_alerts()

        self.assertFalse(published)
        mocked_urlopen.assert_not_called()
        state = self.app.get_home_assistant_alert_state()
        self.assertEqual(state["status"], "preview")
        self.assertIn("Home Assistant", state["message"])

    def test_inventory_change_requests_alert_sensor_refresh(self):
        self.app.HOME_ASSISTANT_ALERT_EVENT.clear()

        self.create_item("Kaffe", quantity="1", min_quantity="2")

        self.assertTrue(self.app.HOME_ASSISTANT_ALERT_EVENT.is_set())

    def test_reading_alerts_does_not_request_another_refresh(self):
        self.app.HOME_ASSISTANT_ALERT_EVENT.clear()

        self.app.create_alerts_payload()

        self.assertFalse(self.app.HOME_ASSISTANT_ALERT_EVENT.is_set())

    def test_organize_page_has_compact_real_alert_setup(self):
        self.app.set_home_assistant_alert_state(
            "connected",
            "sensor.hjemmelager_varsler er oppdatert i Home Assistant.",
        )

        content = self.app.organize_page()

        self.assertNotIn("Systemstatus", content)
        self.assertIn('<details class="card form-section"', content)
        self.assertIn("Sensor klar i Home Assistant", content)
        self.assertIn("Importer varseloppsett", content)
        self.assertIn("blueprint_import", content)
        self.assertIn("sensor.hjemmelager_varsler", content)

    def test_help_page_covers_main_workflows_and_can_be_searched(self):
        content = self.app.help_page()

        self.assertEqual(content.count('class="card form-section help-topic"'), 11)
        for topic_id in (
            "scan",
            "varer",
            "lager",
            "holdbarhet",
            "naering",
            "handleliste",
            "organisering",
            "nfc",
            "varsler",
            "sikkerhet",
        ):
            self.assertIn(f'id="{topic_id}"', content)
        self.assertIn('id="help-search"', content)
        self.assertIn("toLocaleLowerCase", content)
        self.assertIn("Gå til", self.app.help_topic(
            "test", "Test", "Test", ("Ett steg",), ".", "Gå til test"
        ))

    def test_question_mark_opens_contextual_help_on_mobile_and_desktop(self):
        scan_content = self.app.page("Scan kode", "<h1>Test</h1>")
        organize_content = self.app.organize_page()

        self.assertIn(
            'class="help-link" href="help#scan" aria-label="Hjelp"',
            scan_content,
        )
        self.assertIn("header nav", scan_content)
        self.assertIn("display: none", scan_content)
        self.assertIn(".help-link", scan_content)
        self.assertIn('href="help">Hjelp og veiledning</a>', organize_content)

    def test_help_release_version_is_consistent(self):
        addon_dir = Path(__file__).parents[1]
        config = (addon_dir / "config.yaml").read_text(encoding="utf-8")
        docs = (addon_dir / "DOCS.md").read_text(encoding="utf-8")
        changelog = (addon_dir / "CHANGELOG.md").read_text(encoding="utf-8")

        self.assertEqual(self.app.APP_VERSION, "1.3.1")
        self.assertIn('version: "1.3.1"', config)
        self.assertIn("1.3.1 - Hjelp underveis", docs)
        self.assertIn("1.3.1 - Hjelp underveis", changelog)

    def test_alert_blueprint_uses_mobile_app_and_published_sensor(self):
        blueprint_path = Path(__file__).parents[1] / "blueprints" / "daily_inventory_alert.yaml"
        content = blueprint_path.read_text(encoding="utf-8")

        self.assertIn("domain: automation", content)
        self.assertIn("default: sensor.hjemmelager_varsler", content)
        self.assertIn("integration: mobile_app", content)
        self.assertIn("condition: numeric_state", content)
        self.assertIn("type: notify", content)

    def test_product_lookup_fills_name_brand_and_local_image(self):
        payload = json.dumps(
            {
                "status": "success",
                "product": {
                    "code": "1234567890123",
                    "product_name_no": "Testpålegg",
                    "brands": "Testmerket",
                    "quantity": "250 g",
                    "serving_size": "25 g",
                    "nutriments": {
                        "energy-kcal_100g": 220,
                        "energy-kcal_serving": 55,
                        "fat_100g": 12.5,
                        "saturated-fat_100g": 3.1,
                        "carbohydrates_100g": 8,
                        "sugars_100g": 2.4,
                        "fiber_100g": 1.2,
                        "proteins_100g": 15,
                        "salt_100g": 0.8,
                    },
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
        self.assertEqual(product["nutrition"]["energy_kcal_100g"], 220)
        self.assertEqual(product["nutrition"]["energy_kcal_serving"], 55)
        self.assertEqual(product["nutrition"]["serving_size"], 25)
        self.assertEqual(product["nutrition"]["serving_unit"], "g")
        self.assertEqual(product["nutrition"]["proteins_100g"], 15)
        self.assertTrue(product["image_data"].startswith("data:image/jpeg;base64,"))

    def test_forced_product_lookup_bypasses_24_hour_cache(self):
        payloads = [
            {
                "status": "success",
                "product": {
                    "code": "1234567890123",
                    "product_name_no": "Testvare",
                    "nutriments": {"energy-kcal_100g": 100},
                },
            },
            {
                "status": "success",
                "product": {
                    "code": "1234567890123",
                    "product_name_no": "Testvare",
                    "nutriments": {"energy-kcal_100g": 125},
                },
            },
        ]

        with mock.patch.object(
            self.app,
            "urlopen",
            side_effect=[
                FakeResponse(json.dumps(payload).encode(), "application/json")
                for payload in payloads
            ],
        ) as mocked_urlopen:
            first = self.app.lookup_product("1234567890123")
            cached = self.app.lookup_product("1234567890123")
            refreshed = self.app.lookup_product("1234567890123", force_refresh=True)

        self.assertEqual(first["nutrition"]["energy_kcal_100g"], 100)
        self.assertEqual(cached["nutrition"]["energy_kcal_100g"], 100)
        self.assertEqual(refreshed["nutrition"]["energy_kcal_100g"], 125)
        self.assertEqual(mocked_urlopen.call_count, 2)

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

    def test_page_has_keyboard_and_screen_reader_support(self):
        content = self.app.page("Varer", "<h1>Test</h1>")

        self.assertIn('class="skip-link"', content)
        self.assertIn('id="main-content" tabindex="-1"', content)
        self.assertIn('class="app-version"', content)
        self.assertIn(
            f'aria-label="Versjon {self.app.APP_VERSION}"',
            content,
        )
        self.assertIn('role="status" aria-live="polite"', content)
        self.assertIn("prefers-reduced-motion", content)

    def test_upgrade_from_early_database_keeps_existing_inventory(self):
        original_path = self.app.DB_PATH
        legacy_path = Path(self.temp_dir.name) / "legacy-upgrade.db"
        try:
            conn = self.app.sqlite3.connect(legacy_path)
            try:
                conn.execute(
                    """
                    create table items (
                        id integer primary key autoincrement,
                        name text not null,
                        kind text not null default 'consumable',
                        quantity real not null default 0,
                        unit text not null default 'stk',
                        min_quantity real not null default 0,
                        location text not null default '',
                        category text not null default '',
                        tag_id text unique,
                        image_url text not null default '',
                        note text not null default '',
                        shopping_enabled integer not null default 1,
                        last_scanned_at integer,
                        created_at integer not null,
                        updated_at integer not null
                    )
                    """
                )
                conn.execute(
                    """
                    insert into items (
                        name, quantity, unit, min_quantity, location, category,
                        shopping_enabled, created_at, updated_at
                    ) values ('Gammel vare', 7, 'stk', 2, 'Bod', 'Test', 1, 1, 1)
                    """
                )
                conn.commit()
            finally:
                conn.close()
            self.app.DB_PATH = legacy_path
            self.app.init_db()

            item = self.app.list_items()[0]

            self.assertEqual(item["name"], "Gammel vare")
            self.assertEqual(item["quantity"], 7)
            self.assertEqual(item["location"], "Bod")
            self.assertEqual(item["opened_quantity"], 0)
            self.assertEqual(item["target_quantity"], 0)
            self.assertEqual(item["nutrition"], {})
        finally:
            self.app.DB_PATH = original_path


if __name__ == "__main__":
    unittest.main()
