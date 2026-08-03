#!/usr/bin/env python3
import base64
import csv
import html
import io
import json
import os
import sqlite3
import threading
import time
import unicodedata
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from difflib import SequenceMatcher
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, urlencode, parse_qs, unquote, urlparse
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

try:
    import websocket
except ImportError:
    websocket = None


APP_NAME = "Hjemmelager"
APP_VERSION = "1.1.2"
APP_CODENAME = "Kompakt liste"
TAG_LINK_TTL_SECONDS = 180
DATA_DIR = Path(os.environ.get("HJEMMELAGER_DATA_DIR", "./data"))
DB_PATH = DATA_DIR / "hjemmelager.db"
APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"
PORT = int(os.environ.get("HJEMMELAGER_PORT", "8099"))
MAX_IMAGE_UPLOAD_BYTES = 8_000_000
MAX_STORED_IMAGE_BYTES = 2_000_000
MAX_BACKUP_UPLOAD_BYTES = 25_000_000
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
OPEN_FOOD_FACTS_BASE_URL = "https://world.openfoodfacts.org"
OPEN_FOOD_FACTS_USER_AGENT = (
    f"{APP_NAME}/{APP_VERSION} (https://github.com/Geirgutt/tr-kker)"
)
PRODUCT_LOOKUP_CACHE = {}
PRODUCT_LOOKUP_CACHE_SECONDS = 24 * 60 * 60
BACKUP_ITEM_COLUMNS = (
    "id",
    "name",
    "kind",
    "quantity",
    "opened_quantity",
    "unit",
    "min_quantity",
    "target_quantity",
    "price",
    "best_before",
    "expiry_batches_json",
    "location",
    "category",
    "tag_id",
    "barcode",
    "image_url",
    "note",
    "shopping_enabled",
    "shopping_checked",
    "last_scanned_at",
    "created_at",
    "updated_at",
)
BACKUP_REGISTRY_COLUMNS = ("id", "name", "created_at")
BACKUP_LOCATION_TAG_COLUMNS = (
    "id",
    "location",
    "tag_id",
    "last_scanned_at",
    "created_at",
    "updated_at",
)
BACKUP_EVENT_COLUMNS = (
    "id",
    "item_id",
    "action",
    "delta",
    "quantity_after",
    "note",
    "created_at",
)
HOME_ASSISTANT_WEBSOCKET_URL = os.environ.get(
    "HOME_ASSISTANT_WEBSOCKET_URL",
    "ws://supervisor/core/websocket",
)
HOME_ASSISTANT_NFC_LOCK = threading.Lock()
HOME_ASSISTANT_NFC_STATE = {
    "status": "starting",
    "message": "Kobler til Home Assistant …",
    "updated_at": 0,
}
ADDON_SLUG_CACHE = None


def now():
    return int(time.time())


def set_home_assistant_nfc_state(status, message):
    with HOME_ASSISTANT_NFC_LOCK:
        HOME_ASSISTANT_NFC_STATE.update(
            {"status": status, "message": message, "updated_at": now()}
        )


def get_home_assistant_nfc_state():
    with HOME_ASSISTANT_NFC_LOCK:
        return dict(HOME_ASSISTANT_NFC_STATE)


def get_addon_slug():
    global ADDON_SLUG_CACHE
    override = os.environ.get("HJEMMELAGER_ADDON_SLUG", "").strip()
    if override:
        return override
    if ADDON_SLUG_CACHE is not None:
        return ADDON_SLUG_CACHE

    token = os.environ.get("SUPERVISOR_TOKEN", "").strip()
    if not token:
        return ""
    request = Request(
        "http://supervisor/addons/self/info",
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urlopen(request, timeout=3) as response:
            payload = json.load(response)
        ADDON_SLUG_CACHE = str((payload.get("data") or {}).get("slug") or "").strip()
    except (HTTPError, URLError, OSError, ValueError):
        ADDON_SLUG_CACHE = ""
    return ADDON_SLUG_CACHE


def direct_nfc_links(tag_id, addon_slug):
    tag_id = str(tag_id or "").strip()
    addon_slug = str(addon_slug or "").strip()
    if not tag_id or not addon_slug:
        return {"android": "", "iphone": ""}
    tag_fragment = quote(tag_id, safe="")
    panel_path = f"/hassio/ingress/{quote(addon_slug, safe='')}"
    android = (
        f"homeassistant://navigate{panel_path}"
        f"?server=default#hjemmelager-tag={tag_fragment}"
    )
    iphone = (
        "https://www.home-assistant.io/ios/nfc/?url="
        + quote(android, safe="")
    )
    return {"android": android, "iphone": iphone}


def handle_home_assistant_event(message):
    if message.get("type") != "event":
        return None
    event = message.get("event") or {}
    if event.get("event_type") != "tag_scanned":
        return None
    tag_id = str((event.get("data") or {}).get("tag_id") or "").strip()
    if not tag_id:
        return None
    result = touch_tag(tag_id)
    print(
        f"Home Assistant NFC: mottok tagg {tag_id!r}, resultat {result['status']}.",
        flush=True,
    )
    return result


def home_assistant_event_listener():
    token = os.environ.get("SUPERVISOR_TOKEN", "").strip()
    if not token:
        set_home_assistant_nfc_state(
            "preview",
            "Automatisk NFC testes i Home Assistant etter oppdatering.",
        )
        print(
            "Home Assistant NFC-lytter er ikke aktiv i lokal forhåndsvisning.",
            flush=True,
        )
        return
    if websocket is None:
        set_home_assistant_nfc_state(
            "error",
            "NFC-tilkoblingen kunne ikke startes.",
        )
        print(
            "Home Assistant NFC-lytter mangler WebSocket-biblioteket.",
            flush=True,
        )
        return

    retry_seconds = 2
    while True:
        connection = None
        try:
            set_home_assistant_nfc_state(
                "connecting",
                "Kobler til Home Assistant …",
            )
            connection = websocket.create_connection(
                HOME_ASSISTANT_WEBSOCKET_URL,
                timeout=65,
            )
            auth_message = json.loads(connection.recv())
            if auth_message.get("type") != "auth_required":
                raise RuntimeError("uventet svar før autentisering")
            connection.send(json.dumps({"type": "auth", "access_token": token}))
            auth_result = json.loads(connection.recv())
            if auth_result.get("type") != "auth_ok":
                raise RuntimeError("Home Assistant avviste tilkoblingen")
            connection.send(
                json.dumps(
                    {
                        "id": 1,
                        "type": "subscribe_events",
                        "event_type": "tag_scanned",
                    }
                )
            )
            subscription = json.loads(connection.recv())
            if not subscription.get("success"):
                raise RuntimeError("kunne ikke abonnere på tag_scanned")
            print(
                "Home Assistant NFC-lytter er tilkoblet og klar.",
                flush=True,
            )
            set_home_assistant_nfc_state(
                "connected",
                "Klar til å motta NFC-skanningen.",
            )
            retry_seconds = 2

            while True:
                try:
                    raw_message = connection.recv()
                except websocket.WebSocketTimeoutException:
                    connection.ping()
                    continue
                if not raw_message:
                    raise RuntimeError("tilkoblingen ble lukket")
                handle_home_assistant_event(json.loads(raw_message))
        except Exception as exc:
            set_home_assistant_nfc_state(
                "retrying",
                "Mistet forbindelsen til Home Assistant. Prøver igjen automatisk …",
            )
            print(
                f"Home Assistant NFC-lytter kobler til på nytt: {exc}",
                flush=True,
            )
            time.sleep(retry_seconds)
            retry_seconds = min(retry_seconds * 2, 30)
        finally:
            if connection is not None:
                try:
                    connection.close()
                except Exception:
                    pass


def start_home_assistant_event_listener():
    listener = threading.Thread(
        target=home_assistant_event_listener,
        name="home-assistant-nfc",
        daemon=True,
    )
    listener.start()
    return listener


@contextmanager
def db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("pragma foreign_keys = on")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with db() as conn:
        conn.executescript(
            """
            create table if not exists items (
                id integer primary key autoincrement,
                name text not null,
                kind text not null default 'consumable',
                quantity real not null default 0,
                opened_quantity real not null default 0,
                unit text not null default 'stk',
                min_quantity real not null default 0,
                target_quantity real not null default 0,
                price real not null default 0,
                best_before text not null default '',
                expiry_batches_json text not null default '[]',
                location text not null default '',
                category text not null default '',
                tag_id text unique,
                barcode text not null default '',
                image_url text not null default '',
                note text not null default '',
                shopping_enabled integer not null default 1,
                shopping_checked integer not null default 0,
                last_scanned_at integer,
                created_at integer not null,
                updated_at integer not null
            );

            create table if not exists locations (
                id integer primary key autoincrement,
                name text not null unique,
                created_at integer not null
            );

            create table if not exists categories (
                id integer primary key autoincrement,
                name text not null unique,
                created_at integer not null
            );

            create table if not exists events (
                id integer primary key autoincrement,
                item_id integer,
                action text not null,
                delta real,
                quantity_after real,
                note text not null default '',
                created_at integer not null,
                foreign key (item_id) references items(id) on delete cascade
            );

            create table if not exists location_tags (
                id integer primary key autoincrement,
                location text not null unique,
                tag_id text not null unique,
                last_scanned_at integer,
                created_at integer not null,
                updated_at integer not null
            );

            create table if not exists tag_link_sessions (
                id integer primary key check (id = 1),
                item_id integer not null,
                status text not null default 'waiting',
                tag_id text not null default '',
                message text not null default '',
                started_at integer not null,
                expires_at integer not null,
                updated_at integer not null,
                foreign key (item_id) references items(id) on delete cascade
            );

            create table if not exists location_tag_link_sessions (
                id integer primary key check (id = 1),
                location text not null,
                status text not null default 'waiting',
                tag_id text not null default '',
                message text not null default '',
                started_at integer not null,
                expires_at integer not null,
                updated_at integer not null
            );

            create table if not exists deleted_items (
                id integer primary key autoincrement,
                original_item_id integer not null,
                item_json text not null,
                events_json text not null default '[]',
                deleted_at integer not null
            );
            """
        )
        columns = {row["name"] for row in conn.execute("pragma table_info(items)").fetchall()}
        if "barcode" not in columns:
            conn.execute("alter table items add column barcode text not null default ''")
        if "opened_quantity" not in columns:
            conn.execute("alter table items add column opened_quantity real not null default 0")
        if "price" not in columns:
            conn.execute("alter table items add column price real not null default 0")
        if "target_quantity" not in columns:
            conn.execute("alter table items add column target_quantity real not null default 0")
        if "best_before" not in columns:
            conn.execute("alter table items add column best_before text not null default ''")
        if "expiry_batches_json" not in columns:
            conn.execute("alter table items add column expiry_batches_json text not null default '[]'")
        legacy_expiry_rows = conn.execute(
            """
            select id, quantity, best_before, expiry_batches_json
            from items
            where best_before != ''
            """
        ).fetchall()
        for row in legacy_expiry_rows:
            if parse_expiry_batches(row["expiry_batches_json"]):
                continue
            quantity = max(0, float(row["quantity"] or 0))
            if quantity <= 0:
                continue
            batches = [{"best_before": row["best_before"], "quantity": quantity}]
            conn.execute(
                "update items set expiry_batches_json = ? where id = ?",
                (serialize_expiry_batches(batches), row["id"]),
            )
        if "shopping_checked" not in columns:
            conn.execute("alter table items add column shopping_checked integer not null default 0")
        for table, column in (("locations", "location"), ("categories", "category")):
            values = conn.execute(
                f"select distinct trim({column}) as name from items where trim({column}) != ''"
            ).fetchall()
            for row in values:
                conn.execute(
                    f"insert or ignore into {table} (name, created_at) values (?, ?)",
                    (row["name"], now()),
                )


def parse_expiry_batches(value):
    if isinstance(value, list):
        raw_batches = value
    else:
        try:
            raw_batches = json.loads(value or "[]")
        except (TypeError, ValueError, json.JSONDecodeError):
            raw_batches = []
    combined = {}
    for batch in raw_batches if isinstance(raw_batches, list) else []:
        if not isinstance(batch, dict):
            continue
        best_before = str(batch.get("best_before") or "").strip()
        quantity = max(0, parse_float(batch.get("quantity")))
        try:
            date.fromisoformat(best_before)
        except ValueError:
            continue
        if quantity > 0:
            combined[best_before] = combined.get(best_before, 0) + quantity
    return [
        {"best_before": best_before, "quantity": quantity}
        for best_before, quantity in sorted(combined.items())
    ]


def serialize_expiry_batches(batches):
    return json.dumps(parse_expiry_batches(batches), ensure_ascii=False, separators=(",", ":"))


def earliest_best_before(batches):
    parsed = parse_expiry_batches(batches)
    return parsed[0]["best_before"] if parsed else ""


def consume_expiry_batches(batches, quantity):
    remaining = max(0, float(quantity or 0))
    kept = []
    consumed = []
    for batch in parse_expiry_batches(batches):
        take = min(float(batch["quantity"]), remaining)
        if take > 0:
            consumed.append({"best_before": batch["best_before"], "quantity": take})
            remaining -= take
        left = float(batch["quantity"]) - take
        if left > 0:
            kept.append({"best_before": batch["best_before"], "quantity": left})
    return kept, consumed


def merge_expiry_batches(batches, additions):
    return parse_expiry_batches(parse_expiry_batches(batches) + parse_expiry_batches(additions))


def row_to_item(row):
    item = dict(row)
    item["expiry_batches"] = parse_expiry_batches(item.get("expiry_batches_json"))
    item["dated_quantity"] = sum(
        float(batch["quantity"]) for batch in item["expiry_batches"]
    )
    item["undated_quantity"] = max(
        0, float(item["quantity"] or 0) - item["dated_quantity"]
    )
    item["is_low"] = (
        item["kind"] == "consumable"
        and item["shopping_enabled"] == 1
        and item["min_quantity"] > 0
        and item["quantity"] <= item["min_quantity"]
    )
    item["days_until_best_before"] = None
    item["is_expired"] = False
    item["expires_soon"] = False
    if item["kind"] == "consumable" and item["best_before"]:
        try:
            days_left = (date.fromisoformat(item["best_before"]) - date.today()).days
        except ValueError:
            pass
        else:
            item["days_until_best_before"] = days_left
            item["is_expired"] = days_left < 0
            item["expires_soon"] = 0 <= days_left <= 14
    return item


def list_items(where="", params=(), sort="default"):
    query = "select * from items"
    if where:
        query += f" where {where}"
    if sort == "best_before":
        query += " order by best_before, lower(name)"
    else:
        query += """
            order by
                case
                    when kind = 'consumable'
                        and shopping_enabled = 1
                        and min_quantity > 0
                        and quantity <= min_quantity
                    then 1
                    else 0
                end desc,
                lower(name)
        """
    with db() as conn:
        rows = conn.execute(query, params).fetchall()
    return [row_to_item(row) for row in rows]


def count_items(kind=""):
    query = "select count(*) as total from items"
    params = ()
    if kind:
        query += " where kind = ?"
        params = (kind,)
    with db() as conn:
        row = conn.execute(query, params).fetchone()
    return int(row["total"])


def dashboard_summary():
    alerts = create_alerts_payload()
    with db() as conn:
        total = int(conn.execute("select count(*) as total from items").fetchone()["total"])
        recent = conn.execute(
            """
            select events.*, items.name as item_name
            from events
            left join items on items.id = events.item_id
            order by events.id desc
            limit 1
            """
        ).fetchone()
    return {
        "total": total,
        "low_stock": alerts["summary"]["low_stock"],
        "best_before": alerts["summary"]["best_before"],
        "recent": dict(recent) if recent else None,
    }


EVENT_LABELS = {
    "created": "opprettet",
    "updated": "oppdatert",
    "adjusted": "lager endret",
    "adjustment_undone": "lagerendring angret",
    "opened_adjusted": "åpent antall endret",
    "package_opened": "pakke åpnet",
    "expiry_batch_added": "holdbarhetsparti lagt til",
    "expiry_date_removed": "holdbarhetsdato fjernet",
    "tag_linked": "NFC-tag koblet",
    "tag_unlinked": "NFC-tag fjernet",
    "deletion_undone": "sletting angret",
}


def recent_events(limit=50):
    limit = max(1, min(int(limit), 200))
    with db() as conn:
        rows = conn.execute(
            """
            select events.*, items.name as item_name
            from events
            left join items on items.id = events.item_id
            order by events.id desc
            limit ?
            """,
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def format_event_time(timestamp):
    return datetime.fromtimestamp(int(timestamp)).strftime("%d.%m.%Y kl. %H:%M")


def event_description(event):
    name = event.get("item_name") or "Slettet vare"
    action = EVENT_LABELS.get(event.get("action"), event.get("action") or "endret")
    delta = event.get("delta")
    detail = ""
    if delta not in (None, 0):
        detail = f" ({'+' if float(delta) > 0 else ''}{fmt_num(delta)})"
    return f"{name}: {action}{detail}"


def inventory_csv_bytes():
    output = io.StringIO(newline="")
    writer = csv.writer(output, delimiter=";")
    writer.writerow(
        [
            "Navn",
            "Type",
            "Antall",
            "Enhet",
            "Minimum",
            "Fyll opp til",
            "Kategori",
            "Plassering",
            "Best før",
            "Pris",
            "Strekkode",
            "NFC-tag",
            "Notat",
        ]
    )
    for item in list_items():
        writer.writerow(
            [
                item["name"],
                "Forbruksvare" if item["kind"] == "consumable" else "Gjenstand",
                fmt_num(item["quantity"]),
                item["unit"],
                fmt_num(item["min_quantity"]),
                fmt_num(item["target_quantity"]),
                item["category"],
                item["location"],
                item["best_before"],
                fmt_price(item["price"]),
                item["barcode"],
                item["tag_id"] or "",
                item["note"],
            ]
        )
    return ("\ufeff" + output.getvalue()).encode("utf-8")


def distinct_values(column):
    tables = {"category": "categories", "location": "locations"}
    table = tables.get(column)
    if not table:
        return []
    with db() as conn:
        rows = conn.execute(f"select name from {table} order by lower(name)").fetchall()
    return [row["name"] for row in rows]


def create_backup_payload():
    with db() as conn:
        items = [dict(row) for row in conn.execute("select * from items order by id")]
        locations = [dict(row) for row in conn.execute("select * from locations order by id")]
        location_tags = [
            dict(row) for row in conn.execute("select * from location_tags order by id")
        ]
        categories = [dict(row) for row in conn.execute("select * from categories order by id")]
        events = [dict(row) for row in conn.execute("select * from events order by id")]
    return {
        "format": "hjemmelager-backup",
        "format_version": 1,
        "app_version": APP_VERSION,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "data": {
            "items": items,
            "locations": locations,
            "location_tags": location_tags,
            "categories": categories,
            "events": events,
        },
    }


def parse_backup_bytes(raw):
    if not raw:
        raise ValueError("Velg en sikkerhetskopifil")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Filen er ikke en gyldig Hjemmelager-sikkerhetskopi") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("format") != "hjemmelager-backup"
        or payload.get("format_version") != 1
    ):
        raise ValueError("Filen har ukjent sikkerhetskopiformat")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise ValueError("Sikkerhetskopien mangler data")
    for table in ("items", "locations", "categories", "events"):
        if not isinstance(data.get(table), list):
            raise ValueError(f"Sikkerhetskopien mangler tabellen {table}")
        if any(not isinstance(row, dict) for row in data[table]):
            raise ValueError(f"Sikkerhetskopien har ugyldige rader i {table}")
    if "location_tags" not in data:
        data["location_tags"] = []
    if not isinstance(data["location_tags"], list) or any(
        not isinstance(row, dict) for row in data["location_tags"]
    ):
        raise ValueError("Sikkerhetskopien har ugyldige plasseringstagger")
    for item in data["items"]:
        if not isinstance(item, dict) or not str(item.get("name") or "").strip():
            raise ValueError("Sikkerhetskopien inneholder en ugyldig vare")
    return payload


def restore_backup_payload(payload):
    before_payload = create_backup_payload()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    before_filename = f"hjemmelager-before-restore-{timestamp}.json"
    before_path = (DATA_DIR / before_filename).resolve()
    if DATA_DIR.resolve() not in before_path.parents:
        raise ValueError("Ugyldig sikkerhetskopibane")
    before_path.write_text(
        json.dumps(before_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    data = payload["data"]
    defaults = {
        "kind": "consumable",
        "quantity": 0,
        "opened_quantity": 0,
        "unit": "stk",
        "min_quantity": 0,
        "target_quantity": 0,
        "price": 0,
        "best_before": "",
        "expiry_batches_json": "[]",
        "location": "",
        "category": "",
        "tag_id": None,
        "barcode": "",
        "image_url": "",
        "note": "",
        "shopping_enabled": 1,
        "shopping_checked": 0,
        "last_scanned_at": None,
        "created_at": now(),
        "updated_at": now(),
    }

    with db() as conn:
        conn.execute("delete from location_tag_link_sessions")
        conn.execute("delete from tag_link_sessions")
        conn.execute("delete from deleted_items")
        conn.execute("delete from events")
        conn.execute("delete from items")
        conn.execute("delete from location_tags")
        conn.execute("delete from locations")
        conn.execute("delete from categories")

        for table in ("locations", "categories"):
            placeholders = ",".join("?" for _ in BACKUP_REGISTRY_COLUMNS)
            columns = ",".join(BACKUP_REGISTRY_COLUMNS)
            for row in data[table]:
                values = (
                    row.get("id"),
                    str(row.get("name") or "").strip(),
                    row.get("created_at") or now(),
                )
                if not values[1]:
                    continue
                conn.execute(
                    f"insert into {table} ({columns}) values ({placeholders})",
                    values,
                )

        location_tag_placeholders = ",".join("?" for _ in BACKUP_LOCATION_TAG_COLUMNS)
        location_tag_columns = ",".join(BACKUP_LOCATION_TAG_COLUMNS)
        valid_locations = {
            row["name"] for row in conn.execute("select name from locations").fetchall()
        }
        for row in data["location_tags"]:
            location = str(row.get("location") or "").strip()
            tag_id = str(row.get("tag_id") or "").strip()
            if not location or not tag_id or location not in valid_locations:
                continue
            timestamp = now()
            values = (
                row.get("id"),
                location,
                tag_id,
                row.get("last_scanned_at"),
                row.get("created_at") or timestamp,
                row.get("updated_at") or timestamp,
            )
            conn.execute(
                f"insert into location_tags ({location_tag_columns}) values ({location_tag_placeholders})",
                values,
            )

        item_placeholders = ",".join("?" for _ in BACKUP_ITEM_COLUMNS)
        item_columns = ",".join(BACKUP_ITEM_COLUMNS)
        for row in data["items"]:
            values = []
            for column in BACKUP_ITEM_COLUMNS:
                if column == "id":
                    values.append(row.get("id"))
                elif column == "name":
                    values.append(str(row.get("name") or "").strip())
                elif column == "expiry_batches_json":
                    restored_batches = row.get("expiry_batches_json")
                    if restored_batches is None:
                        restored_best_before = str(row.get("best_before") or "").strip()
                        restored_quantity = max(0, parse_float(row.get("quantity")))
                        restored_batches = serialize_expiry_batches(
                            [
                                {
                                    "best_before": restored_best_before,
                                    "quantity": restored_quantity,
                                }
                            ]
                            if restored_best_before and restored_quantity > 0
                            else []
                        )
                    values.append(restored_batches)
                else:
                    values.append(row.get(column, defaults.get(column)))
            conn.execute(
                f"insert into items ({item_columns}) values ({item_placeholders})",
                values,
            )

        valid_item_ids = {
            row["id"] for row in conn.execute("select id from items").fetchall()
        }
        event_placeholders = ",".join("?" for _ in BACKUP_EVENT_COLUMNS)
        event_columns = ",".join(BACKUP_EVENT_COLUMNS)
        for row in data["events"]:
            item_id = row.get("item_id")
            if item_id is not None and item_id not in valid_item_ids:
                continue
            values = [
                row.get("id"),
                item_id,
                row.get("action") or "restored",
                row.get("delta"),
                row.get("quantity_after"),
                row.get("note") or "",
                row.get("created_at") or now(),
            ]
            conn.execute(
                f"insert into events ({event_columns}) values ({event_placeholders})",
                values,
            )

    return {
        "items": len(data["items"]),
        "locations": len(data["locations"]),
        "location_tags": len(data["location_tags"]),
        "categories": len(data["categories"]),
        "events": len(data["events"]),
        "before_filename": before_filename,
    }


def registry_value(data, field):
    selected = (data.get(field) or "").strip()
    new_value = (data.get(f"new_{field}") or "").strip()
    return new_value or selected


def save_registry_value(conn, table, value):
    value = (value or "").strip()
    if value:
        conn.execute(f"insert or ignore into {table} (name, created_at) values (?, ?)", (value, now()))


def create_registry_entry(kind, name):
    tables = {"location": "locations", "category": "categories"}
    table = tables.get(kind)
    name = (name or "").strip()
    if not table or not name:
        return
    with db() as conn:
        save_registry_value(conn, table, name)


def build_item_filters(
    search="",
    category="",
    location="",
    low_only=False,
    kind="",
    expiry_only=False,
):
    clauses = []
    params = []
    if kind in ("consumable", "thing"):
        clauses.append("kind = ?")
        params.append(kind)
    if search:
        clauses.append("(name like ? or location like ? or category like ? or tag_id like ? or barcode like ? or note like ?)")
        params.extend([f"%{search}%"] * 6)
    if category:
        clauses.append("category = ?")
        params.append(category)
    if location:
        clauses.append("location = ?")
        params.append(location)
    if low_only:
        clauses.append("kind = 'consumable' and shopping_enabled = 1 and min_quantity > 0 and quantity <= min_quantity")
    if expiry_only:
        clauses.append("kind = 'consumable' and best_before != '' and best_before <= ?")
        params.append((date.today() + timedelta(days=14)).isoformat())
    return " and ".join(clauses), tuple(params)


def normalized_search_text(value):
    value = unicodedata.normalize("NFKD", str(value or "").lower())
    value = "".join(char for char in value if not unicodedata.combining(char))
    return " ".join(
        "".join(char if char.isalnum() else " " for char in value).split()
    )


def item_matches_search(item, search):
    query = normalized_search_text(search)
    if not query:
        return True
    searchable = normalized_search_text(
        " ".join(
            str(item.get(field) or "")
            for field in ("name", "location", "category", "tag_id", "barcode", "note")
        )
    )
    if query in searchable:
        return True
    words = searchable.split()
    for token in query.split():
        if not any(
            word.startswith(token)
            or token.startswith(word)
            or (
                min(len(token), len(word)) >= 4
                and SequenceMatcher(None, token, word).ratio() >= 0.74
            )
            for word in words
        ):
            return False
    return True


def create_alerts_payload(days=14):
    try:
        days = int(days)
    except (TypeError, ValueError):
        days = 14
    days = max(1, min(days, 90))
    threshold = (date.today() + timedelta(days=days)).isoformat()
    low_items = list_items(
        "kind = 'consumable' and shopping_enabled = 1 "
        "and min_quantity > 0 and quantity <= min_quantity"
    )
    expiry_items = list_items(
        "kind = 'consumable' and best_before != '' and best_before <= ?",
        (threshold,),
        sort="best_before",
    )

    low_stock = []
    for item in low_items:
        target = float(item["target_quantity"] or 0)
        if target <= 0:
            target = float(item["min_quantity"] or 0)
        low_stock.append(
            {
                "id": item["id"],
                "name": item["name"],
                "quantity": item["quantity"],
                "unit": item["unit"],
                "buy_quantity": max(1, target - float(item["quantity"] or 0)),
                "location": item["location"],
            }
        )

    best_before = []
    expired_count = 0
    for item in expiry_items:
        days_left = item["days_until_best_before"]
        if days_left is None:
            continue
        if days_left < 0:
            status = "expired"
            expired_count += 1
        elif days_left == 0:
            status = "today"
        else:
            status = "soon"
        best_before.append(
            {
                "id": item["id"],
                "name": item["name"],
                "best_before": item["best_before"],
                "days_left": days_left,
                "status": status,
                "location": item["location"],
            }
        )

    message_parts = []
    if low_stock:
        names = ", ".join(
            f"{entry['name']} ({fmt_num(entry['buy_quantity'])} {entry['unit']})"
            for entry in low_stock
        )
        message_parts.append(f"Må kjøpes: {names}.")
    if best_before:
        def expiry_text(entry):
            if entry["days_left"] < 0:
                timing = "utløpt"
            elif entry["days_left"] == 0:
                timing = "i dag"
            else:
                timing = f"{entry['days_left']} dager"
            return f"{entry['name']} ({timing})"

        message_parts.append(
            "Best før: " + ", ".join(expiry_text(entry) for entry in best_before) + "."
        )

    unique_item_ids = {
        entry["id"] for entry in low_stock
    } | {
        entry["id"] for entry in best_before
    }
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "days_ahead": days,
        "summary": {
            "total": len(unique_item_ids),
            "low_stock": len(low_stock),
            "best_before": len(best_before),
            "expired": expired_count,
        },
        "message": " ".join(message_parts) or "Ingen varer krever oppmerksomhet.",
        "low_stock": low_stock,
        "best_before": best_before,
    }


def get_item(item_id):
    with db() as conn:
        row = conn.execute("select * from items where id = ?", (item_id,)).fetchone()
    return row_to_item(row) if row else None


def get_item_by_tag(tag_id):
    with db() as conn:
        row = conn.execute("select * from items where tag_id = ?", (tag_id,)).fetchone()
    return row_to_item(row) if row else None


def get_item_by_barcode(barcode):
    with db() as conn:
        row = conn.execute("select * from items where barcode = ?", (barcode,)).fetchone()
    return row_to_item(row) if row else None


def parse_float(value, fallback=0.0):
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return fallback


def esc(value):
    return html.escape("" if value is None else str(value), quote=True)


def fmt_num(value):
    value = float(value or 0)
    return str(int(value)) if value.is_integer() else f"{value:g}"


def fmt_price(value):
    value = float(value or 0)
    return "" if value <= 0 else f"{value:.2f}".rstrip("0").rstrip(".")


def image_value(data, existing=""):
    if data.get("remove_image"):
        return ""
    if data.get("image_file_data_url"):
        value = data["image_file_data_url"]
        prefix, separator, encoded = value.partition(",")
        allowed_prefixes = tuple(
            f"data:{content_type};base64" for content_type in ALLOWED_IMAGE_TYPES
        )
        if not separator or not prefix.lower().startswith(allowed_prefixes):
            raise ValueError("Bildet har et format som ikke støttes")
        estimated_size = len(encoded) * 3 // 4
        if estimated_size > MAX_STORED_IMAGE_BYTES:
            raise ValueError("Bildet er fortsatt for stort etter behandling. Velg et mindre bilde")
        return value
    return (data.get("image_url") or existing or "").strip()


def parse_content_disposition(value):
    parts = [part.strip() for part in value.split(";")]
    params = {}
    for part in parts[1:]:
        if "=" not in part:
            continue
        key, raw_value = part.split("=", 1)
        params[key.strip().lower()] = raw_value.strip().strip('"')
    return params


def parse_multipart_form(raw, content_type):
    boundary_marker = "boundary="
    if boundary_marker not in content_type:
        raise ValueError("Missing multipart boundary")
    boundary = content_type.split(boundary_marker, 1)[1].split(";", 1)[0].strip().strip('"')
    delimiter = b"--" + boundary.encode("utf-8")
    data = {}

    for part in raw.split(delimiter):
        part = part.strip(b"\r\n")
        if not part or part == b"--":
            continue
        if part.endswith(b"--"):
            part = part[:-2].rstrip(b"\r\n")
        if b"\r\n\r\n" not in part:
            continue
        raw_headers, content = part.split(b"\r\n\r\n", 1)
        headers = {}
        for line in raw_headers.decode("utf-8", "replace").split("\r\n"):
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            headers[key.lower().strip()] = value.strip()

        disposition = headers.get("content-disposition", "")
        params = parse_content_disposition(disposition)
        name = params.get("name")
        if not name:
            continue

        filename = params.get("filename", "")
        content = content.rstrip(b"\r\n")
        if filename:
            if not content:
                continue
            if name == "backup_file":
                if len(content) > MAX_BACKUP_UPLOAD_BYTES:
                    raise ValueError("Sikkerhetskopien er for stor. Maks 25 MB")
                data["backup_file_bytes"] = content
                data["backup_file_name"] = Path(filename).name
                continue
            content_type = headers.get("content-type", "application/octet-stream").split(";", 1)[0].lower()
            if content_type not in ALLOWED_IMAGE_TYPES:
                raise ValueError("Bildet må være JPEG, PNG, WebP eller GIF")
            if len(content) > MAX_IMAGE_UPLOAD_BYTES:
                raise ValueError("Bildet er for stort. Maks 8 MB")
            data[f"{name}_data_url"] = f"data:{content_type};base64,{base64.b64encode(content).decode('ascii')}"
        else:
            data[name] = content.decode("utf-8", "replace")

    return data


def save_event(conn, item_id, action, delta=None, quantity_after=None, note=""):
    conn.execute(
        """
        insert into events (item_id, action, delta, quantity_after, note, created_at)
        values (?, ?, ?, ?, ?, ?)
        """,
        (item_id, action, delta, quantity_after, note, now()),
    )


def create_item(data):
    timestamp = now()
    tag_id = (data.get("tag_id") or "").strip() or None
    barcode = (data.get("barcode") or "").strip()
    quantity = parse_float(data.get("quantity"))
    best_before = (data.get("best_before") or "").strip()
    expiry_batches = (
        [{"best_before": best_before, "quantity": quantity}]
        if best_before and quantity > 0
        else []
    )
    opened_quantity = parse_float(data.get("opened_quantity"))
    location = registry_value(data, "location")
    category = registry_value(data, "category")
    with db() as conn:
        if tag_id and conn.execute(
            "select 1 from location_tags where tag_id = ?", (tag_id,)
        ).fetchone():
            raise sqlite3.IntegrityError("tag_id already exists")
        save_registry_value(conn, "locations", location)
        save_registry_value(conn, "categories", category)
        cur = conn.execute(
            """
            insert into items (
                name, kind, quantity, opened_quantity, unit, min_quantity, target_quantity, price, best_before, expiry_batches_json,
                location, category, tag_id, barcode, image_url, note, shopping_enabled, created_at, updated_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                (data.get("name") or "Uten navn").strip(),
                data.get("kind") or "consumable",
                quantity,
                opened_quantity,
                (data.get("unit") or "stk").strip(),
                parse_float(data.get("min_quantity")),
                parse_float(data.get("target_quantity")),
                parse_float(data.get("price")),
                best_before,
                serialize_expiry_batches(expiry_batches),
                location,
                category,
                tag_id,
                barcode,
                image_value(data),
                (data.get("note") or "").strip(),
                1 if str(data.get("shopping_enabled", "1")).lower() in ("1", "true", "on", "yes") else 0,
                timestamp,
                timestamp,
            ),
        )
        item_id = cur.lastrowid
        save_event(conn, item_id, "created", None, quantity)
    return get_item(item_id)


def new_item_redirect(item, data):
    if str(data.get("link_nfc_after_save", "0")).lower() in (
        "1",
        "true",
        "on",
        "yes",
    ):
        start_tag_link(item["id"])
        return f"item/{item['id']}/tag-link"
    return f"item/{item['id']}?created=1"


def created_item_notice(item):
    noun = "Gjenstanden" if item["kind"] == "thing" else "Varen"
    return f"""
      <section class="created-notice">
        <span class="created-check" aria-hidden="true">✓</span>
        <h2>{noun} er lagt til</h2>
        <p class="muted">Hva vil du gjøre videre?</p>
        <div class="actions">
          <form method="post" action="item/{item['id']}/tag-link/start">
            <button class="btn primary">Koble NFC-tag</button>
          </form>
          <a class="btn" href="item/{item['id']}/edit">Legg til detaljer</a>
          <a class="btn" href="new">Legg til en ny</a>
        </div>
      </section>
    """


def update_item(item_id, data):
    existing = get_item(item_id)
    if not existing:
        return None
    timestamp = now()
    tag_id = (data.get("tag_id") or "").strip() or None
    barcode = (data.get("barcode") or "").strip()
    location = registry_value(data, "location")
    category = registry_value(data, "category")
    previous_quantity = float(existing["quantity"] or 0)
    quantity = max(0, parse_float(data.get("quantity"), previous_quantity))
    expiry_batches = existing["expiry_batches"]
    if "best_before" in data:
        submitted_best_before = (data.get("best_before") or "").strip()
        expiry_batches = (
            [{"best_before": submitted_best_before, "quantity": quantity}]
            if submitted_best_before and quantity > 0
            else []
        )
    elif quantity < previous_quantity:
        expiry_batches, _ = consume_expiry_batches(
            expiry_batches, previous_quantity - quantity
        )
    if (data.get("kind") or existing["kind"]) != "consumable":
        expiry_batches = []
    best_before = earliest_best_before(expiry_batches)
    with db() as conn:
        if tag_id and conn.execute(
            "select 1 from location_tags where tag_id = ?", (tag_id,)
        ).fetchone():
            raise sqlite3.IntegrityError("tag_id already exists")
        save_registry_value(conn, "locations", location)
        save_registry_value(conn, "categories", category)
        conn.execute(
            """
            update items set
                name = ?, kind = ?, quantity = ?, opened_quantity = ?, unit = ?, min_quantity = ?, target_quantity = ?,
                price = ?, best_before = ?, expiry_batches_json = ?,
                location = ?, category = ?, tag_id = ?, barcode = ?, image_url = ?, note = ?,
                shopping_enabled = ?, shopping_checked = 0, updated_at = ?
            where id = ?
            """,
            (
                (data.get("name") or existing["name"]).strip(),
                data.get("kind") or existing["kind"],
                quantity,
                parse_float(data.get("opened_quantity"), existing["opened_quantity"]),
                (data.get("unit") or existing["unit"]).strip(),
                parse_float(data.get("min_quantity"), existing["min_quantity"]),
                parse_float(data.get("target_quantity"), existing["target_quantity"]),
                parse_float(data.get("price"), existing["price"]),
                best_before,
                serialize_expiry_batches(expiry_batches),
                location,
                category,
                tag_id,
                barcode,
                image_value(data, existing["image_url"]),
                (data.get("note") or "").strip(),
                1 if str(data.get("shopping_enabled", "1")).lower() in ("1", "true", "on", "yes") else 0,
                timestamp,
                item_id,
            ),
        )
        save_event(conn, item_id, "updated", None, quantity)
    return get_item(item_id)


def adjust_item(item_id, delta, note=""):
    with db() as conn:
        row = conn.execute("select * from items where id = ?", (item_id,)).fetchone()
        if not row:
            return None
        previous_quantity = float(row["quantity"])
        quantity = max(0, previous_quantity + float(delta))
        actual_delta = quantity - previous_quantity
        expiry_batches = parse_expiry_batches(row["expiry_batches_json"])
        consumed = []
        if actual_delta < 0:
            expiry_batches, consumed = consume_expiry_batches(
                expiry_batches, -actual_delta
            )
        event_note = note
        if consumed:
            event_note = json.dumps(
                {"source": note, "consumed_expiry_batches": consumed},
                ensure_ascii=False,
                separators=(",", ":"),
            )
        conn.execute(
            """
            update items
            set quantity = ?,
                best_before = ?,
                expiry_batches_json = ?,
                shopping_checked = case when ? > 0 then 0 else shopping_checked end,
                updated_at = ?
            where id = ?
            """,
            (
                quantity,
                earliest_best_before(expiry_batches),
                serialize_expiry_batches(expiry_batches),
                actual_delta,
                now(),
                item_id,
            ),
        )
        save_event(conn, item_id, "adjusted", actual_delta, quantity, event_note)
    return get_item(item_id)


def add_expiry_batch(
    item_id,
    quantity,
    best_before,
    note="web",
    from_existing=False,
):
    quantity = parse_float(quantity)
    best_before = str(best_before or "").strip()
    if quantity <= 0:
        raise ValueError("Antallet må være større enn null")
    try:
        date.fromisoformat(best_before)
    except ValueError as exc:
        raise ValueError("Velg en gyldig holdbarhetsdato") from exc
    with db() as conn:
        row = conn.execute("select * from items where id = ?", (item_id,)).fetchone()
        if not row:
            return None
        if row["kind"] != "consumable":
            raise ValueError("Holdbarhetspartier kan bare brukes på forbruksvarer")
        existing_batches = parse_expiry_batches(row["expiry_batches_json"])
        if from_existing:
            dated_quantity = sum(float(batch["quantity"]) for batch in existing_batches)
            undated_quantity = max(0, float(row["quantity"] or 0) - dated_quantity)
            if quantity > undated_quantity + 0.000001:
                raise ValueError(
                    f"Bare {fmt_num(undated_quantity)} {row['unit']} mangler dato"
                )
        batches = merge_expiry_batches(
            existing_batches,
            [{"best_before": best_before, "quantity": quantity}],
        )
        new_quantity = float(row["quantity"] or 0)
        if not from_existing:
            new_quantity += quantity
        conn.execute(
            """
            update items
            set quantity = ?, best_before = ?, expiry_batches_json = ?,
                shopping_checked = 0, updated_at = ?
            where id = ?
            """,
            (
                new_quantity,
                earliest_best_before(batches),
                serialize_expiry_batches(batches),
                now(),
                item_id,
            ),
        )
        save_event(
            conn,
            item_id,
            "expiry_batch_added",
            0 if from_existing else quantity,
            new_quantity,
            (
                f"{best_before}:existing"
                if from_existing
                else best_before
            )
            if note == "web"
            else f"{note}:{best_before}",
        )
    return get_item(item_id)


def clear_expiry_batch_date(item_id, best_before, note="web"):
    best_before = str(best_before or "").strip()
    with db() as conn:
        row = conn.execute("select * from items where id = ?", (item_id,)).fetchone()
        if not row:
            return None
        batches = [
            batch
            for batch in parse_expiry_batches(row["expiry_batches_json"])
            if batch["best_before"] != best_before
        ]
        conn.execute(
            """
            update items
            set best_before = ?, expiry_batches_json = ?, updated_at = ?
            where id = ?
            """,
            (
                earliest_best_before(batches),
                serialize_expiry_batches(batches),
                now(),
                item_id,
            ),
        )
        save_event(
            conn,
            item_id,
            "expiry_date_removed",
            None,
            row["quantity"],
            best_before if note == "web" else f"{note}:{best_before}",
        )
    return get_item(item_id)


def undo_last_adjustment(item_id, max_age_seconds=600):
    timestamp = now()
    with db() as conn:
        item = conn.execute("select * from items where id = ?", (item_id,)).fetchone()
        if not item:
            return None
        event = conn.execute(
            """
            select * from events
            where item_id = ?
            order by id desc
            limit 1
            """,
            (item_id,),
        ).fetchone()
        if (
            not event
            or event["action"] != "adjusted"
            or event["delta"] is None
            or timestamp - int(event["created_at"]) > max_age_seconds
        ):
            return {"status": "unavailable", "item": row_to_item(item)}
        previous_quantity = max(
            0,
            float(event["quantity_after"] or 0) - float(event["delta"]),
        )
        expiry_batches = parse_expiry_batches(item["expiry_batches_json"])
        try:
            event_note = json.loads(event["note"] or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            event_note = {}
        if isinstance(event_note, dict):
            expiry_batches = merge_expiry_batches(
                expiry_batches,
                event_note.get("consumed_expiry_batches") or [],
            )
        conn.execute(
            """
            update items
            set quantity = ?, best_before = ?, expiry_batches_json = ?, updated_at = ?
            where id = ?
            """,
            (
                previous_quantity,
                earliest_best_before(expiry_batches),
                serialize_expiry_batches(expiry_batches),
                timestamp,
                item_id,
            ),
        )
        save_event(
            conn,
            item_id,
            "adjustment_undone",
            -float(event["delta"]),
            previous_quantity,
            f"undo:{event['id']}",
        )
    return {"status": "undone", "item": get_item(item_id)}


def adjustment_notice(item):
    return f"""
      <section class="created-notice">
        <div>
          <h2>Lageret er oppdatert</h2>
          <p class="muted">Feil trykk? Du kan angre den siste endringen.</p>
        </div>
        <form method="post" action="item/{item['id']}/undo-adjustment">
          <button class="btn">Angre siste endring</button>
        </form>
      </section>
    """


def deletion_notice(deletion_id):
    return f"""
      <section class="created-notice">
        <div>
          <h2>Varen er slettet</h2>
          <p class="muted">Var det en feil? Varen og historikken kan hentes tilbake nå.</p>
        </div>
        <form method="post" action="deleted/{int(deletion_id)}/restore">
          <button class="btn">Angre sletting</button>
        </form>
      </section>
    """


def set_shopping_checked(item_id, checked):
    with db() as conn:
        row = conn.execute("select * from items where id = ?", (item_id,)).fetchone()
        if not row:
            return None
        value = 1 if checked else 0
        conn.execute(
            "update items set shopping_checked = ?, updated_at = ? where id = ?",
            (value, now(), item_id),
        )
        save_event(
            conn,
            item_id,
            "shopping_checked" if value else "shopping_unchecked",
            None,
            row["quantity"],
            "web",
        )
    return get_item(item_id)


def set_shopping_enabled(item_id, enabled):
    with db() as conn:
        row = conn.execute("select * from items where id = ?", (item_id,)).fetchone()
        if not row:
            return None
        conn.execute(
            """
            update items
            set shopping_enabled = ?, shopping_checked = 0, updated_at = ?
            where id = ?
            """,
            (1 if enabled else 0, now(), item_id),
        )
        save_event(
            conn,
            item_id,
            "shopping_enabled" if enabled else "shopping_disabled",
            None,
            row["quantity"],
        )
    return get_item(item_id)


def delete_item(item_id):
    with db() as conn:
        row = conn.execute("select * from items where id = ?", (item_id,)).fetchone()
        if not row:
            return None
        events = [
            dict(event)
            for event in conn.execute(
                "select * from events where item_id = ? order by id",
                (item_id,),
            ).fetchall()
        ]
        cursor = conn.execute(
            """
            insert into deleted_items
                (original_item_id, item_json, events_json, deleted_at)
            values (?, ?, ?, ?)
            """,
            (
                item_id,
                json.dumps(dict(row), ensure_ascii=False),
                json.dumps(events, ensure_ascii=False),
                now(),
            ),
        )
        conn.execute("delete from items where id = ?", (item_id,))
        conn.execute(
            """
            delete from deleted_items
            where id not in (
                select id from deleted_items order by id desc limit 20
            )
            """
        )
        return int(cursor.lastrowid)


def restore_deleted_item(deletion_id):
    with db() as conn:
        deletion = conn.execute(
            "select * from deleted_items where id = ?",
            (deletion_id,),
        ).fetchone()
        if not deletion:
            return {"status": "not_found"}
        item = json.loads(deletion["item_json"])
        existing = conn.execute(
            "select id from items where id = ?",
            (item["id"],),
        ).fetchone()
        if existing:
            return {"status": "conflict", "message": "Vare-ID-en er allerede i bruk."}
        tag_id = item.get("tag_id")
        if tag_id and conn.execute(
            "select id from items where tag_id = ?",
            (tag_id,),
        ).fetchone():
            return {
                "status": "conflict",
                "message": "NFC-taggen er allerede koblet til en annen vare.",
            }
        columns = ",".join(BACKUP_ITEM_COLUMNS)
        placeholders = ",".join("?" for _ in BACKUP_ITEM_COLUMNS)
        restored_expiry_batches = item.get("expiry_batches_json")
        if restored_expiry_batches is None:
            restored_expiry_batches = serialize_expiry_batches(
                [
                    {
                        "best_before": item.get("best_before"),
                        "quantity": item.get("quantity"),
                    }
                ]
                if item.get("best_before") and float(item.get("quantity") or 0) > 0
                else []
            )
        conn.execute(
            f"insert into items ({columns}) values ({placeholders})",
            tuple(
                restored_expiry_batches
                if column == "expiry_batches_json"
                else item.get(column)
                for column in BACKUP_ITEM_COLUMNS
            ),
        )
        for event in json.loads(deletion["events_json"]):
            conn.execute(
                """
                insert into events
                    (item_id, action, delta, quantity_after, note, created_at)
                values (?, ?, ?, ?, ?, ?)
                """,
                (
                    item["id"],
                    event.get("action") or "updated",
                    event.get("delta"),
                    event.get("quantity_after"),
                    event.get("note") or "",
                    event.get("created_at") or now(),
                ),
            )
        save_event(
            conn,
            item["id"],
            "deletion_undone",
            None,
            item["quantity"],
        )
        conn.execute("delete from deleted_items where id = ?", (deletion_id,))
    return {"status": "restored", "item": get_item(item["id"])}


def adjust_opened_item(item_id, delta, note=""):
    with db() as conn:
        row = conn.execute("select * from items where id = ?", (item_id,)).fetchone()
        if not row:
            return None
        opened_quantity = max(0, float(row["opened_quantity"]) + float(delta))
        conn.execute(
            "update items set opened_quantity = ?, updated_at = ? where id = ?",
            (opened_quantity, now(), item_id),
        )
        save_event(conn, item_id, "opened_adjusted", delta, row["quantity"], note)
    return get_item(item_id)


def open_package(item_id, note=""):
    with db() as conn:
        row = conn.execute("select * from items where id = ?", (item_id,)).fetchone()
        if not row:
            return None
        previous_quantity = float(row["quantity"])
        quantity = max(0, previous_quantity - 1)
        actual_delta = quantity - previous_quantity
        opened_quantity = float(row["opened_quantity"]) - actual_delta
        expiry_batches = parse_expiry_batches(row["expiry_batches_json"])
        if actual_delta < 0:
            expiry_batches, _ = consume_expiry_batches(expiry_batches, -actual_delta)
        conn.execute(
            """
            update items
            set quantity = ?, opened_quantity = ?, best_before = ?,
                expiry_batches_json = ?, updated_at = ?
            where id = ?
            """,
            (
                quantity,
                opened_quantity,
                earliest_best_before(expiry_batches),
                serialize_expiry_batches(expiry_batches),
                now(),
                item_id,
            ),
        )
        save_event(conn, item_id, "package_opened", actual_delta, quantity, note)
    return get_item(item_id)


def start_tag_link(item_id):
    item = get_item(item_id)
    if not item:
        return None
    timestamp = now()
    with db() as conn:
        conn.execute("delete from location_tag_link_sessions")
        conn.execute("delete from tag_link_sessions")
        conn.execute(
            """
            insert into tag_link_sessions
                (id, item_id, status, tag_id, message, started_at, expires_at, updated_at)
            values (1, ?, 'waiting', '', '', ?, ?, ?)
            """,
            (item_id, timestamp, timestamp + TAG_LINK_TTL_SECONDS, timestamp),
        )
    return get_tag_link_session(item_id)


def get_location_tag(location):
    location = (location or "").strip()
    if not location:
        return None
    with db() as conn:
        row = conn.execute(
            "select * from location_tags where location = ?", (location,)
        ).fetchone()
    return dict(row) if row else None


def get_location_tag_by_tag_id(tag_id):
    tag_id = (tag_id or "").strip()
    if not tag_id:
        return None
    with db() as conn:
        row = conn.execute(
            "select * from location_tags where tag_id = ?", (tag_id,)
        ).fetchone()
    return dict(row) if row else None


def start_location_tag_link(location):
    location = (location or "").strip()
    if not location or location not in distinct_values("location"):
        return None
    timestamp = now()
    with db() as conn:
        conn.execute("delete from tag_link_sessions")
        conn.execute("delete from location_tag_link_sessions")
        conn.execute(
            """
            insert into location_tag_link_sessions
                (id, location, status, tag_id, message, started_at, expires_at, updated_at)
            values (1, ?, 'waiting', '', '', ?, ?, ?)
            """,
            (location, timestamp, timestamp + TAG_LINK_TTL_SECONDS, timestamp),
        )
    return get_location_tag_link_session(location)


def get_location_tag_link_session(location=None):
    with db() as conn:
        row = conn.execute(
            "select * from location_tag_link_sessions where id = 1"
        ).fetchone()
        if not row or (location is not None and row["location"] != location):
            return None
        session = dict(row)
        if session["status"] == "waiting" and session["expires_at"] <= now():
            message = "Tiden løp ut uten at en tag ble skannet."
            conn.execute(
                "update location_tag_link_sessions set status = 'expired', message = ?, updated_at = ? where id = 1",
                (message, now()),
            )
            session["status"] = "expired"
            session["message"] = message
        session["seconds_left"] = max(0, session["expires_at"] - now())
        return session


def cancel_location_tag_link(location):
    with db() as conn:
        conn.execute(
            """
            update location_tag_link_sessions
            set status = 'cancelled', message = 'Koblingen ble avbrutt.', updated_at = ?
            where id = 1 and location = ? and status = 'waiting'
            """,
            (now(), location),
        )
    return get_location_tag_link_session(location)


def get_tag_link_session(item_id=None):
    with db() as conn:
        row = conn.execute("select * from tag_link_sessions where id = 1").fetchone()
        if not row or (item_id is not None and row["item_id"] != item_id):
            return None
        session = dict(row)
        if session["status"] == "waiting" and session["expires_at"] <= now():
            message = "Tiden løp ut uten at en tag ble skannet."
            conn.execute(
                "update tag_link_sessions set status = 'expired', message = ?, updated_at = ? where id = 1",
                (message, now()),
            )
            session["status"] = "expired"
            session["message"] = message
        session["seconds_left"] = max(0, session["expires_at"] - now())
        return session


def cancel_tag_link(item_id):
    with db() as conn:
        conn.execute(
            """
            update tag_link_sessions
            set status = 'cancelled', message = 'Koblingen ble avbrutt.', updated_at = ?
            where id = 1 and item_id = ? and status = 'waiting'
            """,
            (now(), item_id),
        )
    return get_tag_link_session(item_id)


def touch_tag(tag_id):
    tag_id = (tag_id or "").strip()
    if not tag_id:
        return {"status": "not_found", "tag_id": ""}

    timestamp = now()
    result = None
    with db() as conn:
        session_row = conn.execute(
            """
            select * from tag_link_sessions
            where id = 1 and status = 'waiting' and expires_at > ?
            """,
            (timestamp,),
        ).fetchone()
        location_session_row = conn.execute(
            """
            select * from location_tag_link_sessions
            where id = 1 and status = 'waiting' and expires_at > ?
            """,
            (timestamp,),
        ).fetchone()
        linked_row = conn.execute("select * from items where tag_id = ?", (tag_id,)).fetchone()
        linked_location_row = conn.execute(
            "select * from location_tags where tag_id = ?", (tag_id,)
        ).fetchone()

        if session_row:
            target_row = conn.execute(
                "select * from items where id = ?", (session_row["item_id"],)
            ).fetchone()
            if not target_row:
                message = "Varen finnes ikke lenger."
                conn.execute(
                    """
                    update tag_link_sessions
                    set status = 'cancelled', message = ?, updated_at = ?
                    where id = 1
                    """,
                    (message, timestamp),
                )
                return {"status": "cancelled", "tag_id": tag_id, "message": message}

            if (linked_row and linked_row["id"] != target_row["id"]) or linked_location_row:
                existing_name = (
                    linked_row["name"] if linked_row else linked_location_row["location"]
                )
                message = f'Taggen er allerede koblet til «{existing_name}».'
                conn.execute(
                    """
                    update tag_link_sessions
                    set status = 'conflict', tag_id = ?, message = ?, updated_at = ?
                    where id = 1
                    """,
                    (tag_id, message, timestamp),
                )
                result = {
                    "status": "conflict",
                    "tag_id": tag_id,
                    "message": message,
                    "existing_item_id": linked_row["id"] if linked_row else None,
                    "existing_item_name": existing_name,
                }
            else:
                conn.execute(
                    """
                    update items
                    set tag_id = ?, last_scanned_at = ?, updated_at = ?
                    where id = ?
                    """,
                    (tag_id, timestamp, timestamp, target_row["id"]),
                )
                message = f'Taggen er koblet til «{target_row["name"]}».'
                conn.execute(
                    """
                    update tag_link_sessions
                    set status = 'linked', tag_id = ?, message = ?, updated_at = ?
                    where id = 1
                    """,
                    (tag_id, message, timestamp),
                )
                save_event(
                    conn,
                    target_row["id"],
                    "tag_linked",
                    None,
                    target_row["quantity"],
                    tag_id,
                )
                result = {
                    "status": "linked",
                    "tag_id": tag_id,
                    "message": message,
                    "item_id": target_row["id"],
                }
        elif location_session_row:
            location = location_session_row["location"]
            location_exists = conn.execute(
                "select 1 from locations where name = ?", (location,)
            ).fetchone()
            if not location_exists:
                message = "Plasseringen finnes ikke lenger."
                conn.execute(
                    "update location_tag_link_sessions set status = 'cancelled', message = ?, updated_at = ? where id = 1",
                    (message, timestamp),
                )
                return {"status": "cancelled", "tag_id": tag_id, "message": message}

            conflict = linked_row or (
                linked_location_row and linked_location_row["location"] != location
            )
            if conflict:
                existing_name = (
                    linked_row["name"] if linked_row else linked_location_row["location"]
                )
                message = f'Taggen er allerede koblet til «{existing_name}».'
                conn.execute(
                    """
                    update location_tag_link_sessions
                    set status = 'conflict', tag_id = ?, message = ?, updated_at = ?
                    where id = 1
                    """,
                    (tag_id, message, timestamp),
                )
                result = {
                    "status": "conflict",
                    "tag_id": tag_id,
                    "message": message,
                }
            else:
                conn.execute(
                    """
                    insert into location_tags
                        (location, tag_id, last_scanned_at, created_at, updated_at)
                    values (?, ?, ?, ?, ?)
                    on conflict(location) do update set
                        tag_id = excluded.tag_id,
                        last_scanned_at = excluded.last_scanned_at,
                        updated_at = excluded.updated_at
                    """,
                    (location, tag_id, timestamp, timestamp, timestamp),
                )
                message = f'Taggen er koblet til plasseringen «{location}».'
                conn.execute(
                    """
                    update location_tag_link_sessions
                    set status = 'linked', tag_id = ?, message = ?, updated_at = ?
                    where id = 1
                    """,
                    (tag_id, message, timestamp),
                )
                result = {
                    "status": "linked",
                    "tag_id": tag_id,
                    "message": message,
                    "location": location,
                }
        elif linked_row:
            conn.execute(
                "update items set last_scanned_at = ?, updated_at = ? where id = ?",
                (timestamp, timestamp, linked_row["id"]),
            )
            save_event(
                conn,
                linked_row["id"],
                "tag_scanned",
                None,
                linked_row["quantity"],
                tag_id,
            )
            result = {"status": "touched", "tag_id": tag_id, "item_id": linked_row["id"]}
        elif linked_location_row:
            conn.execute(
                "update location_tags set last_scanned_at = ?, updated_at = ? where id = ?",
                (timestamp, timestamp, linked_location_row["id"]),
            )
            result = {
                "status": "touched",
                "tag_id": tag_id,
                "location": linked_location_row["location"],
            }
        else:
            result = {"status": "not_found", "tag_id": tag_id}

    if result.get("item_id"):
        result["item"] = get_item(result["item_id"])
    return result


def download_product_image(image_url):
    parsed = urlparse(image_url or "")
    if parsed.scheme != "https" or parsed.hostname != "images.openfoodfacts.org":
        return ""
    request = Request(
        image_url,
        headers={
            "User-Agent": OPEN_FOOD_FACTS_USER_AGENT,
            "Accept": "image/jpeg,image/png,image/webp",
        },
    )
    try:
        with urlopen(request, timeout=6) as response:
            content_type = response.headers.get_content_type()
            if content_type not in ALLOWED_IMAGE_TYPES:
                return ""
            raw = response.read(MAX_IMAGE_UPLOAD_BYTES + 1)
            if len(raw) > MAX_IMAGE_UPLOAD_BYTES:
                return ""
    except (HTTPError, URLError, TimeoutError, OSError):
        return ""
    return f"data:{content_type};base64,{base64.b64encode(raw).decode('ascii')}"


def lookup_product(barcode):
    barcode = (barcode or "").strip()
    if not barcode.isdigit() or not 8 <= len(barcode) <= 14:
        return {
            "status": "not_applicable",
            "barcode": barcode,
            "message": "Koden ser ikke ut som en vanlig produktstrekkode.",
        }

    cached = PRODUCT_LOOKUP_CACHE.get(barcode)
    if cached and cached["cached_at"] + PRODUCT_LOOKUP_CACHE_SECONDS > now():
        return cached["result"]

    fields = ",".join(
        (
            "code",
            "product_name",
            "product_name_no",
            "product_name_en",
            "brands",
            "quantity",
            "image_front_small_url",
        )
    )
    url = (
        f"{OPEN_FOOD_FACTS_BASE_URL}/api/v3.6/product/{barcode}.json?"
        + urlencode({"fields": fields})
    )
    request = Request(
        url,
        headers={
            "User-Agent": OPEN_FOOD_FACTS_USER_AGENT,
            "Accept": "application/json",
        },
    )
    try:
        with urlopen(request, timeout=6) as response:
            payload = json.load(response)
    except HTTPError as exc:
        result = {
            "status": "not_found" if exc.code == 404 else "unavailable",
            "barcode": barcode,
            "message": (
                "Fant ikke produktet i Open Food Facts. Fyll inn varen manuelt."
                if exc.code == 404
                else "Produktoppslaget er midlertidig utilgjengelig."
            ),
        }
    except (URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError):
        result = {
            "status": "unavailable",
            "barcode": barcode,
            "message": "Kunne ikke kontakte Open Food Facts. Du kan fylle inn varen manuelt.",
        }
    else:
        product = payload.get("product") or {}
        if payload.get("status") != "success" or not product:
            result = {
                "status": "not_found",
                "barcode": barcode,
                "message": "Fant ikke produktet i Open Food Facts. Fyll inn varen manuelt.",
            }
        else:
            product_name = (
                product.get("product_name_no")
                or product.get("product_name")
                or product.get("product_name_en")
                or ""
            ).strip()
            if not product_name:
                result = {
                    "status": "not_found",
                    "barcode": barcode,
                    "message": "Produktet mangler navn. Fyll inn varen manuelt.",
                }
            else:
                image_data = download_product_image(product.get("image_front_small_url") or "")
                result = {
                    "status": "found",
                    "barcode": product.get("code") or barcode,
                    "name": product_name,
                    "brand": (product.get("brands") or "").split(",")[0].strip(),
                    "package_size": (product.get("quantity") or "").strip(),
                    "image_data": image_data,
                    "suggested_category": "Matvarer",
                    "suggested_unit": "pk",
                    "source": "Open Food Facts",
                    "source_url": f"https://world.openfoodfacts.org/product/{barcode}",
                    "message": "Produktinformasjon ble funnet.",
                }

    PRODUCT_LOOKUP_CACHE[barcode] = {"cached_at": now(), "result": result}
    return result


def item_id_from_scanned_url(code):
    parsed = urlparse(code)
    if not parsed.scheme:
        return None
    parts = [part for part in parsed.path.split("/") if part]
    for index, part in enumerate(parts):
        if part == "item" and index + 1 < len(parts) and parts[index + 1].isdigit():
            return int(parts[index + 1])
    return None


def scanned_code_redirect(code):
    code = (code or "").strip()
    if not code:
        return "scan"

    item_id = item_id_from_scanned_url(code)
    if item_id and get_item(item_id):
        return f"item/{item_id}"

    item = get_item_by_barcode(code)
    if item:
        return f"item/{item['id']}"

    return "new?" + urlencode({"barcode": code})


def page(title, body, base_path=""):
    base = esc(base_path.rstrip("/") + "/" if base_path else "/")
    active_page = {
        "Varer": "items",
        "Lav beholdning": "low",
        "Scan kode": "scan",
        "Ny vare": "new",
        "Steder og kategorier": "organize",
    }.get(title, "")

    def nav_class(page_name, primary=False):
        classes = ["nav"]
        if primary:
            classes.append("primary")
        if page_name == active_page:
            classes.append("active")
        return " ".join(classes)

    def mobile_nav_class(page_name, primary=False):
        classes = ["mobile-nav-link"]
        if primary:
            classes.append("primary")
        if page_name == active_page:
            classes.append("active")
        return " ".join(classes)

    return f"""<!doctype html>
<html lang="no">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <base href="{base}">
  <title>{esc(title)} - {APP_NAME}</title>
  <style>
    :root {{
      color-scheme: light dark;
      --bg: #f6f4ef;
      --panel: #ffffff;
      --text: #202124;
      --muted: #687076;
      --line: #d9d5ca;
      --accent: #0f766e;
      --accent-2: #bc6c25;
      --danger: #b42318;
      --ok: #1f7a4d;
      --shadow-sm: 0 1px 2px rgb(15 23 42 / 5%), 0 5px 18px rgb(15 23 42 / 4%);
      --radius: 14px;
    }}
    @media (prefers-color-scheme: dark) {{
      :root {{
        --bg: #111417;
        --panel: #1c2024;
        --text: #eff1f2;
        --muted: #a5adb4;
        --line: #343a40;
      }}
    }}
    * {{ box-sizing: border-box; }}
    html {{ scroll-behavior: smooth; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font: 16px/1.45 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    header {{
      position: sticky;
      top: 0;
      z-index: 10;
      background: color-mix(in srgb, var(--panel) 94%, transparent);
      border-bottom: 1px solid var(--line);
      backdrop-filter: blur(10px);
    }}
    .bar, main {{
      width: min(1100px, 100%);
      margin: 0 auto;
      padding: 14px;
    }}
    .bar {{
      display: flex;
      align-items: center;
      gap: 12px;
      justify-content: space-between;
    }}
    .brand {{
      font-weight: 750;
      font-size: 1.16rem;
      color: var(--text);
      text-decoration: none;
      letter-spacing: -.02em;
    }}
    .brand-lockup {{
      display: flex;
      align-items: baseline;
      gap: 6px;
      min-width: 0;
    }}
    .app-version {{
      padding: 1px 6px;
      border: 1px solid var(--line);
      border-radius: 999px;
      color: var(--muted);
      font-size: .68rem;
      font-weight: 700;
      letter-spacing: .01em;
      line-height: 1.45;
      white-space: nowrap;
    }}
    nav {{
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      justify-content: flex-end;
    }}
    a, button {{ touch-action: manipulation; }}
    .skip-link {{
      position: fixed;
      inset: 8px auto auto 8px;
      z-index: 100;
      padding: 9px 12px;
      border-radius: 9px;
      color: white;
      background: var(--accent);
      transform: translateY(-150%);
    }}
    .skip-link:focus {{
      transform: translateY(0);
    }}
    .nav, .btn {{
      border: 1px solid var(--line);
      border-radius: 10px;
      color: var(--text);
      background: var(--panel);
      padding: 8px 11px;
      text-decoration: none;
      font-weight: 650;
      cursor: pointer;
    }}
    .btn:disabled {{
      cursor: wait;
      opacity: .7;
    }}
    .nav.active {{
      color: var(--accent);
      border-color: color-mix(in srgb, var(--accent) 45%, var(--line));
      background: color-mix(in srgb, var(--accent) 8%, var(--panel));
    }}
    .btn.primary, .nav.primary {{
      background: var(--accent);
      border-color: var(--accent);
      color: white;
    }}
    .btn:hover, .nav:hover {{
      border-color: color-mix(in srgb, var(--accent) 55%, var(--line));
    }}
    .btn:focus-visible, .nav:focus-visible, input:focus-visible, select:focus-visible,
    textarea:focus-visible, summary:focus-visible, .mobile-nav-link:focus-visible {{
      outline: 3px solid color-mix(in srgb, var(--accent) 25%, transparent);
      outline-offset: 2px;
    }}
    .btn.warn {{ color: white; background: var(--accent-2); border-color: var(--accent-2); }}
    .btn.danger {{ color: white; background: var(--danger); border-color: var(--danger); }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(250px, 1fr));
      gap: 12px;
    }}
    .dashboard-strip {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 7px;
      margin: 0 0 10px;
    }}
    .dashboard-stat {{
      display: grid;
      gap: 1px;
      min-width: 0;
      padding: 9px 10px;
      border: 1px solid var(--line);
      border-radius: 11px;
      color: var(--muted);
      background: var(--panel);
      font-size: .78rem;
      text-decoration: none;
    }}
    .dashboard-stat strong {{
      color: var(--text);
      font-size: 1.05rem;
      line-height: 1.2;
    }}
    .dashboard-stat.attention strong {{
      color: var(--accent-2);
    }}
    .dashboard-recent {{
      display: flex;
      justify-content: space-between;
      gap: 10px;
      margin: -2px 0 11px;
      color: var(--muted);
      font-size: .8rem;
    }}
    .dashboard-recent a {{
      color: var(--accent);
      white-space: nowrap;
    }}
    .status-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 8px;
      margin-top: 10px;
    }}
    .status-item {{
      display: grid;
      gap: 2px;
      padding: 10px;
      border: 1px solid var(--line);
      border-radius: 11px;
      background: color-mix(in srgb, var(--panel) 88%, var(--bg));
    }}
    .status-item strong {{
      display: flex;
      align-items: center;
      gap: 7px;
    }}
    .status-dot {{
      width: 9px;
      height: 9px;
      border-radius: 50%;
      background: var(--ok);
    }}
    .status-dot.waiting {{
      background: var(--accent-2);
    }}
    .history-list {{
      display: grid;
      gap: 0;
      padding: 0;
      margin: 0;
      list-style: none;
    }}
    .history-row {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 10px;
      padding: 10px 0;
      border-bottom: 1px solid var(--line);
    }}
    .history-row:last-child {{
      border-bottom: 0;
    }}
    .history-row time {{
      color: var(--muted);
      font-size: .8rem;
      white-space: nowrap;
    }}
    .page-heading {{
      display: flex;
      align-items: start;
      justify-content: space-between;
      gap: 12px;
      margin-bottom: 12px;
    }}
    .page-heading h1, .page-heading p {{
      margin: 0;
    }}
    .save-status {{
      position: fixed;
      z-index: 50;
      inset: auto 12px calc(82px + env(safe-area-inset-bottom)) auto;
      max-width: min(300px, calc(100vw - 24px));
      padding: 9px 12px;
      border: 1px solid var(--line);
      border-radius: 10px;
      color: var(--text);
      background: var(--panel);
      box-shadow: var(--shadow-sm);
    }}
    .save-status:empty {{
      display: none;
    }}
    .save-status.increased {{
      border-color: color-mix(in srgb, var(--ok) 55%, var(--line));
      color: var(--ok);
    }}
    .save-status.decreased {{
      border-color: color-mix(in srgb, var(--danger) 55%, var(--line));
      color: var(--danger);
    }}
    [data-quantity-display].quantity-increased {{
      animation: quantity-increased 2.4s ease-out;
    }}
    [data-quantity-display].quantity-decreased {{
      animation: quantity-decreased 2.4s ease-out;
    }}
    @keyframes quantity-increased {{
      0%, 24% {{
        color: var(--ok);
        background: color-mix(in srgb, var(--ok) 16%, transparent);
        border-radius: 7px;
      }}
      100% {{ color: inherit; background: transparent; }}
    }}
    @keyframes quantity-decreased {{
      0%, 24% {{
        color: var(--danger);
        background: color-mix(in srgb, var(--danger) 14%, transparent);
        border-radius: 7px;
      }}
      100% {{ color: inherit; background: transparent; }}
    }}
    @media (prefers-reduced-motion: reduce) {{
      html {{ scroll-behavior: auto; }}
      *, *::before, *::after {{
        animation-duration: .01ms !important;
        animation-iteration-count: 1 !important;
        transition-duration: .01ms !important;
      }}
    }}
    .toolbar {{
      display: grid;
      gap: 10px;
      margin-bottom: 14px;
    }}
    .search-row {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 8px;
      align-items: end;
    }}
    .search-row label {{
      min-width: 0;
    }}
    .filter-panel {{
      border: 0;
    }}
    .filter-panel summary {{
      display: none;
      cursor: pointer;
      font-weight: 750;
    }}
    .filter-panel summary::marker {{
      color: var(--accent);
    }}
    .filters {{
      display: grid;
      grid-template-columns: minmax(160px, 1fr) minmax(160px, 1fr) auto auto auto;
      gap: 8px;
      align-items: end;
    }}
    .view-switch {{
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      align-items: center;
    }}
    .view-switch .btn.active {{
      background: color-mix(in srgb, var(--accent) 12%, var(--panel));
      border-color: var(--accent);
    }}
    .expiry-notice {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      margin: 0 0 10px;
      padding: 8px 10px;
      border: 1px solid #f59e0b;
      border-radius: 10px;
      color: #92400e;
      background: #fff7df;
      font-size: .88rem;
      font-weight: 750;
      text-decoration: none;
    }}
    .expiry-notice svg {{
      flex: 0 0 auto;
      width: 18px;
      height: 18px;
      fill: none;
      stroke: currentColor;
      stroke-linecap: round;
      stroke-linejoin: round;
      stroke-width: 2;
    }}
    .expiry-notice-copy {{
      display: flex;
      align-items: center;
      gap: 7px;
      min-width: 0;
    }}
    .expiry-notice-action {{
      white-space: nowrap;
    }}
    .expiry-filter-label {{
      display: flex;
      align-items: center;
      align-self: center;
      gap: 7px;
      min-height: 42px;
      padding: 0 4px;
      font-size: .88rem;
      white-space: nowrap;
    }}
    .expiry-filter-label input {{
      width: auto;
      margin: 0;
    }}
    .inventory-tabs {{
      display: grid;
      grid-template-columns: repeat(3, auto);
      gap: 6px;
      width: fit-content;
      max-width: 100%;
      padding: 5px;
      border: 1px solid var(--line);
      border-radius: 13px;
      background: color-mix(in srgb, var(--line) 22%, var(--panel));
    }}
    .inventory-tab {{
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 6px;
      min-height: 42px;
      padding: 8px;
      border-radius: 9px;
      color: var(--muted);
      font-weight: 750;
      text-decoration: none;
    }}
    .inventory-tab.active {{
      color: var(--text);
      background: var(--panel);
      box-shadow: var(--shadow-sm);
    }}
    .inventory-tab-count {{
      min-width: 23px;
      padding: 2px 6px;
      border-radius: 999px;
      color: var(--muted);
      background: color-mix(in srgb, var(--line) 45%, transparent);
      font-size: .75rem;
      text-align: center;
    }}
    .inventory-tab.active .inventory-tab-count {{
      color: var(--accent);
      background: color-mix(in srgb, var(--accent) 12%, transparent);
    }}
    .card {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: var(--radius);
      padding: 14px;
      box-shadow: var(--shadow-sm);
    }}
    .empty-state {{
      display: grid;
      justify-items: start;
      gap: 7px;
      padding: 20px;
      border: 1px dashed color-mix(in srgb, var(--muted) 55%, var(--line));
      border-radius: var(--radius);
      background: color-mix(in srgb, var(--panel) 92%, transparent);
    }}
    .grid > .empty-state, .item-list > .empty-state {{
      grid-column: 1 / -1;
    }}
    .empty-state-icon {{
      display: grid;
      place-items: center;
      width: 38px;
      height: 38px;
      border-radius: 11px;
      color: var(--accent);
      background: color-mix(in srgb, var(--accent) 12%, transparent);
    }}
    .empty-state-icon svg {{
      width: 21px;
      height: 21px;
      fill: none;
      stroke: currentColor;
      stroke-linecap: round;
      stroke-linejoin: round;
      stroke-width: 2;
    }}
    .empty-state h2, .empty-state p {{
      margin: 0;
    }}
    .empty-state-actions {{
      display: flex;
      flex-wrap: wrap;
      gap: 7px;
      margin-top: 4px;
    }}
    .empty-state-choices {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 8px;
      width: 100%;
      margin-top: 4px;
    }}
    .empty-choice {{
      display: grid;
      gap: 3px;
      padding: 12px;
      border: 1px solid var(--line);
      border-radius: 11px;
      color: var(--text);
      background: var(--panel);
      text-decoration: none;
    }}
    .empty-choice strong {{
      color: var(--accent);
    }}
    .new-start {{
      max-width: 620px;
      margin-inline: auto;
    }}
    .new-start .empty-state-choices {{
      grid-template-columns: 1fr;
    }}
    .new-start .empty-choice {{
      grid-template-columns: 42px minmax(0, 1fr);
      align-items: center;
      gap: 10px;
      padding: 11px;
    }}
    .new-choice-icon {{
      display: grid;
      place-items: center;
      width: 42px;
      height: 42px;
      border-radius: 11px;
      color: var(--accent);
      background: color-mix(in srgb, var(--accent) 10%, var(--panel));
    }}
    .new-choice-icon svg {{
      width: 22px;
      height: 22px;
      fill: none;
      stroke: currentColor;
      stroke-linecap: round;
      stroke-linejoin: round;
      stroke-width: 2;
    }}
    .new-choice-copy {{
      display: grid;
      gap: 2px;
      min-width: 0;
    }}
    .item-card {{
      position: relative;
      display: grid;
      grid-template-columns: 76px 1fr;
      gap: 12px;
      align-items: start;
      transition: border-color .16s ease, transform .16s ease, box-shadow .16s ease;
    }}
    .item-card:hover {{
      border-color: color-mix(in srgb, var(--accent) 42%, var(--line));
      transform: translateY(-1px);
      box-shadow: 0 10px 28px rgb(15 23 42 / 9%);
    }}
    .item-thumb {{
      width: 76px;
      aspect-ratio: 1;
      border-radius: 11px;
      border: 1px solid var(--line);
      object-fit: contain;
      padding: 4px;
      background: #fff;
    }}
    .item-hero {{
      display: block;
      width: 100%;
      max-height: 320px;
      margin-bottom: 14px;
      padding: 12px;
      border: 1px solid var(--line);
      border-radius: var(--radius);
      object-fit: contain;
      background: #fff;
    }}
    .item-main {{ min-width: 0; }}
    .item-name-link {{
      color: var(--text);
      text-decoration: none;
    }}
    .item-name-link::after {{
      content: "";
      position: absolute;
      inset: 0;
      border-radius: inherit;
    }}
    .item-meta {{
      display: grid;
      gap: 3px;
      font-size: .92rem;
    }}
    .item-meta-line {{
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }}
    .item-card .actions {{
      position: relative;
      z-index: 1;
    }}
    .item-card .qty {{
      margin: 5px 0 0;
      font-size: 1.08rem;
      font-weight: 720;
    }}
    .opened-count {{
      margin-top: 0;
      font-size: .84rem;
    }}
    .item-list {{
      display: grid;
      gap: 6px;
    }}
    .item-row {{
      display: grid;
      grid-template-columns: 40px minmax(0, 1.4fr) minmax(150px, .9fr) auto 18px;
      gap: 9px;
      align-items: center;
      min-height: 54px;
      color: var(--text);
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 6px 9px;
      text-decoration: none;
      transition: border-color .15s ease, background .15s ease;
    }}
    .item-row:hover {{
      border-color: color-mix(in srgb, var(--accent) 45%, var(--line));
      background: color-mix(in srgb, var(--accent) 4%, var(--panel));
    }}
    .item-row-thumb {{
      width: 40px;
      aspect-ratio: 1;
      border-radius: 7px;
      border: 1px solid var(--line);
      object-fit: contain;
      padding: 3px;
      background: #fff;
    }}
    .item-row-title {{
      min-width: 0;
      font-weight: 750;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }}
    .item-row-meta {{
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }}
    .item-row-qty {{
      font-weight: 800;
      white-space: nowrap;
      text-align: right;
    }}
    .item-row-arrow {{
      color: var(--muted);
      font-size: 1.3rem;
      line-height: 1;
    }}
    .location-list {{
      display: grid;
      gap: 8px;
      margin: 14px 0 0;
      padding: 0;
      list-style: none;
    }}
    .location-entry {{
      display: grid;
      gap: 8px;
      padding: 11px;
      border: 1px solid var(--line);
      border-radius: 10px;
    }}
    .location-entry > div:first-child {{
      display: flex;
      align-items: baseline;
      justify-content: space-between;
      gap: 10px;
    }}
    .location-entry .actions {{ margin-top: 0; }}
    .item-title {{
      display: flex;
      align-items: start;
      justify-content: space-between;
      gap: 10px;
      margin-bottom: 8px;
    }}
    h1 {{ font-size: clamp(1.4rem, 2vw, 2rem); margin: 8px 0 16px; letter-spacing: 0; }}
    h2 {{ font-size: 1.05rem; margin: 0; letter-spacing: 0; }}
    .muted {{ color: var(--muted); }}
    .pill {{
      display: inline-flex;
      align-items: center;
      min-height: 26px;
      border-radius: 999px;
      padding: 3px 9px;
      border: 1px solid var(--line);
      color: var(--muted);
      font-size: .85rem;
      white-space: nowrap;
    }}
    .item-badges {{
      display: flex;
      gap: 4px;
      flex-wrap: wrap;
      justify-content: flex-end;
    }}
    .low {{ border-color: #f59e0b; color: #92400e; background: #fef3c7; }}
    .expires-soon {{ border-color: #f59e0b; color: #92400e; background: #fff7df; }}
    .expired {{ border-color: #ef4444; color: #991b1b; background: #fee2e2; }}
    .scanner {{
      width: 100%;
      aspect-ratio: 4 / 3;
      border-radius: 8px;
      border: 1px solid var(--line);
      background: #050608;
      object-fit: cover;
    }}
    .scanner-diagnostics {{
      display: grid;
      grid-template-columns: minmax(140px, max-content) minmax(0, 1fr);
      gap: 6px 10px;
      margin: 0;
      padding: 10px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: color-mix(in srgb, var(--line) 18%, transparent);
      font-size: .9rem;
    }}
    .scanner-diagnostics-wrap {{
      border-top: 1px solid var(--line);
      padding-top: 8px;
    }}
    .scanner-diagnostics-wrap summary {{
      cursor: pointer;
      color: var(--muted);
      font-size: .86rem;
      font-weight: 700;
    }}
    .scanner-diagnostics-wrap[open] summary {{
      margin-bottom: 8px;
    }}
    .scanner-diagnostics dt {{
      color: var(--muted);
      font-weight: 650;
    }}
    .scanner-diagnostics dd {{
      margin: 0;
      min-width: 0;
      overflow-wrap: anywhere;
    }}
    .shopping-header {{
      display: flex;
      align-items: end;
      justify-content: space-between;
      gap: 12px;
      margin-bottom: 10px;
    }}
    .shopping-header h1 {{
      margin-bottom: 3px;
    }}
    .shopping-header p {{
      margin: 0;
    }}
    .shopping-section-title {{
      margin: 16px 0 8px;
      color: var(--muted);
      font-size: .86rem;
      font-weight: 800;
      letter-spacing: .04em;
      text-transform: uppercase;
    }}
    .shopping-list {{
      display: grid;
      gap: 5px;
    }}
    .shopping-groups {{
      display: grid;
      gap: 12px;
    }}
    .shopping-group {{
      display: grid;
      gap: 6px;
    }}
    .shopping-group-heading {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding-inline: 3px;
      color: var(--muted);
      font-size: .82rem;
      font-weight: 750;
      letter-spacing: .025em;
      text-transform: uppercase;
    }}
    .shopping-group-count {{
      color: var(--accent);
    }}
    .shopping-row {{
      display: grid;
      grid-template-columns: 30px 36px minmax(0, 1fr);
      gap: 8px;
      align-items: center;
      padding: 7px 8px;
      border: 1px solid var(--line);
      border-radius: 10px;
      background: var(--panel);
      box-shadow: var(--shadow-sm);
    }}
    .shopping-row.checked {{
      opacity: .62;
    }}
    .shopping-check {{
      display: grid;
      place-items: center;
      width: 30px;
      height: 30px;
      padding: 0;
      border: 2px solid color-mix(in srgb, var(--muted) 65%, var(--line));
      border-radius: 9px;
      color: white;
      background: transparent;
      cursor: pointer;
    }}
    .shopping-row.checked .shopping-check {{
      border-color: var(--ok);
      background: var(--ok);
    }}
    .shopping-check svg {{
      width: 17px;
      height: 17px;
      fill: none;
      stroke: currentColor;
      stroke-linecap: round;
      stroke-linejoin: round;
      stroke-width: 2.5;
    }}
    .shopping-thumb {{
      width: 36px;
      aspect-ratio: 1;
      padding: 3px;
      border: 1px solid var(--line);
      border-radius: 8px;
      object-fit: contain;
      background: #fff;
    }}
    .shopping-copy {{
      min-width: 0;
    }}
    .shopping-name {{
      margin: 0;
      overflow: hidden;
      font-size: .94rem;
      font-weight: 780;
      line-height: 1.12;
      text-overflow: ellipsis;
      white-space: nowrap;
    }}
    .shopping-row.checked .shopping-name {{
      text-decoration: line-through;
    }}
    .shopping-amount {{
      margin-top: 1px;
      color: var(--accent);
      font-size: .84rem;
      font-weight: 750;
      line-height: 1.16;
    }}
    .shopping-meta {{
      margin-top: 1px;
      overflow: hidden;
      color: var(--muted);
      font-size: .72rem;
      line-height: 1.15;
      text-overflow: ellipsis;
      white-space: nowrap;
    }}
    .shopping-completed {{
      margin-top: 14px;
      border: 0;
    }}
    .shopping-completed summary {{
      cursor: pointer;
      color: var(--muted);
      font-weight: 750;
    }}
    .shopping-completed .shopping-list {{
      margin-top: 8px;
    }}
    .qty {{ font-size: 2rem; font-weight: 800; margin: 8px 0; }}
    .actions {{ display: flex; gap: 8px; flex-wrap: wrap; margin-top: 12px; }}
    .quantity-custom {{
      display: flex;
      align-items: end;
      gap: 6px;
      flex: 1 1 210px;
    }}
    .quantity-custom label {{
      flex: 1 1 110px;
      font-size: .82rem;
    }}
    .quantity-custom input {{
      min-width: 92px;
      padding: 8px 9px;
    }}
    .expiry-panel {{
      margin-top: 18px;
      padding-top: 16px;
      border-top: 1px solid var(--line);
    }}
    .expiry-panel h2 {{ margin: 0 0 4px; font-size: 1rem; }}
    .expiry-batch-list {{ display: grid; gap: 6px; margin-top: 10px; }}
    .expiry-batch-row {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      min-height: 40px;
      padding: 7px 9px;
      border: 1px solid var(--line);
      border-radius: 9px;
    }}
    .expiry-add-form {{
      display: grid;
      grid-template-columns: minmax(100px, .7fr) minmax(150px, 1fr) minmax(165px, 1fr) auto;
      align-items: end;
      gap: 8px;
      margin-top: 12px;
    }}
    form.stack, .stack {{ display: grid; gap: 12px; }}
    label {{ display: grid; gap: 5px; font-weight: 650; }}
    input, select, textarea {{
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 10px;
      background: var(--panel);
      color: var(--text);
      padding: 10px;
      font: inherit;
    }}
    textarea {{ min-height: 92px; resize: vertical; }}
    .form-grid {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 12px;
    }}
    .form-card {{
      display: grid;
      gap: 12px;
    }}
    .form-card h2 {{
      margin: 0;
      font-size: 1.05rem;
    }}
    .form-card > p {{
      margin: -5px 0 0;
    }}
    .form-section {{
      padding: 0;
      overflow: clip;
    }}
    .form-section > summary {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      padding: 14px;
      cursor: pointer;
      font-weight: 750;
      list-style: none;
    }}
    .form-section > summary::-webkit-details-marker {{
      display: none;
    }}
    .form-section > summary::after {{
      content: "+";
      color: var(--accent);
      font-size: 1.35rem;
      font-weight: 500;
    }}
    .form-section[open] > summary::after {{
      content: "−";
    }}
    .form-section-summary {{
      display: grid;
      gap: 2px;
    }}
    .form-section-summary small {{
      color: var(--muted);
      font-weight: 500;
    }}
    .form-section-content {{
      padding: 0 14px 14px;
      border-top: 1px solid var(--line);
    }}
    .form-section-content .form-grid {{
      padding-top: 14px;
    }}
    .field-help {{
      color: var(--muted);
      font-size: .84rem;
      font-weight: 500;
    }}
    .field-group {{
      display: grid;
      gap: 6px;
    }}
    .nfc-next-step {{
      display: grid;
      gap: 3px;
      padding: 10px;
      border: 1px solid color-mix(in srgb, var(--accent) 28%, var(--line));
      border-radius: 10px;
      background: color-mix(in srgb, var(--accent) 5%, var(--panel));
    }}
    .nfc-next-step label {{
      display: block;
    }}
    .nfc-next-step input {{
      width: auto;
      margin-right: 5px;
    }}
    .created-notice {{
      display: grid;
      gap: 8px;
      margin-bottom: 10px;
      padding: 11px;
      border: 1px solid color-mix(in srgb, var(--ok) 36%, var(--line));
      border-radius: 12px;
      background: color-mix(in srgb, var(--ok) 8%, var(--panel));
    }}
    .created-notice h2, .created-notice p {{
      margin: 0;
    }}
    .created-check {{
      display: inline-grid;
      place-items: center;
      width: 28px;
      height: 28px;
      border-radius: 50%;
      color: var(--panel);
      background: var(--ok);
      font-weight: 800;
    }}
    .field-label {{
      font-weight: 650;
    }}
    .file-picker {{
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 8px;
      min-height: 46px;
      padding: 10px 12px;
      border: 1px dashed color-mix(in srgb, var(--accent) 55%, var(--line));
      border-radius: 10px;
      color: var(--accent);
      background: color-mix(in srgb, var(--accent) 7%, var(--panel));
      cursor: pointer;
    }}
    .file-picker svg {{
      width: 20px;
      height: 20px;
      fill: none;
      stroke: currentColor;
      stroke-linecap: round;
      stroke-linejoin: round;
      stroke-width: 2;
    }}
    .item-image-preview {{
      display: grid;
      grid-template-columns: 54px minmax(0, 1fr);
      gap: 9px;
      align-items: center;
      padding: 7px;
      border: 1px solid var(--line);
      border-radius: 10px;
      background: color-mix(in srgb, var(--line) 18%, var(--panel));
    }}
    .item-image-preview[hidden] {{
      display: none;
    }}
    .item-image-preview img {{
      width: 54px;
      height: 54px;
      padding: 3px;
      border-radius: 8px;
      object-fit: contain;
      background: white;
    }}
    .item-image-preview strong, .item-image-preview span {{
      display: block;
    }}
    .barcode-step {{
      display: grid;
      gap: 7px;
      padding: 12px;
      border: 1px solid color-mix(in srgb, var(--accent) 35%, var(--line));
      border-radius: 11px;
      background: color-mix(in srgb, var(--accent) 6%, var(--panel));
    }}
    .barcode-scan-link {{
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 8px;
      min-height: 46px;
    }}
    .barcode-scan-link svg {{
      width: 21px;
      height: 21px;
      fill: none;
      stroke: currentColor;
      stroke-linecap: round;
      stroke-linejoin: round;
      stroke-width: 2;
    }}
    .barcode-confirmation {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      flex-wrap: wrap;
    }}
    .product-suggestion {{
      display: grid;
      grid-template-columns: 58px minmax(0, 1fr);
      gap: 10px;
      align-items: center;
      padding-top: 9px;
      border-top: 1px solid color-mix(in srgb, var(--accent) 22%, var(--line));
    }}
    .product-suggestion[hidden] {{
      display: none;
    }}
    .product-suggestion-image {{
      width: 58px;
      aspect-ratio: 1;
      padding: 3px;
      border: 1px solid var(--line);
      border-radius: 9px;
      object-fit: contain;
      background: #fff;
    }}
    .product-suggestion-copy {{
      min-width: 0;
    }}
    .product-suggestion-copy p {{
      margin: 1px 0 0;
    }}
    .product-source {{
      color: var(--muted);
      font-size: .75rem;
    }}
    .product-source a {{
      color: inherit;
    }}
    .tag-link-card {{
      display: grid;
      justify-items: center;
      gap: 12px;
      max-width: 520px;
      margin: 0 auto;
      padding: 24px;
      text-align: center;
    }}
    .tag-link-icon {{
      display: grid;
      place-items: center;
      width: 78px;
      height: 78px;
      border-radius: 50%;
      color: var(--accent);
      background: color-mix(in srgb, var(--accent) 11%, var(--panel));
    }}
    .tag-link-icon.waiting {{
      animation: tag-pulse 1.6s ease-in-out infinite;
    }}
    .tag-link-icon svg {{
      width: 42px;
      height: 42px;
      fill: none;
      stroke: currentColor;
      stroke-linecap: round;
      stroke-linejoin: round;
      stroke-width: 1.8;
    }}
    .tag-link-card h1, .tag-link-card p {{
      margin: 0;
    }}
    .tag-link-status {{
      min-height: 2.7em;
    }}
    .nfc-connection {{
      display: flex;
      align-items: center;
      gap: 7px;
      width: fit-content;
      max-width: 100%;
      padding: 6px 9px;
      border: 1px solid var(--line);
      border-radius: 999px;
      color: var(--muted);
      background: color-mix(in srgb, var(--line) 16%, var(--panel));
      font-size: .82rem;
      line-height: 1.2;
    }}
    .nfc-connection::before {{
      content: "";
      flex: 0 0 auto;
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: var(--muted);
    }}
    .nfc-connection[data-state="connected"] {{
      color: var(--ok);
      border-color: color-mix(in srgb, var(--ok) 35%, var(--line));
      background: color-mix(in srgb, var(--ok) 8%, var(--panel));
    }}
    .nfc-connection[data-state="connected"]::before {{
      background: var(--ok);
      box-shadow: 0 0 0 3px color-mix(in srgb, var(--ok) 16%, transparent);
    }}
    .nfc-connection[data-state="error"] {{
      color: var(--danger);
      border-color: color-mix(in srgb, var(--danger) 35%, var(--line));
    }}
    .nfc-connection[data-state="error"]::before {{
      background: var(--danger);
    }}
    .tag-link-card .actions {{
      justify-content: center;
      margin-top: 2px;
    }}
    .danger-zone {{
      margin-top: 10px;
      padding: 0;
      overflow: clip;
    }}
    .danger-zone > summary {{
      padding: 12px 14px;
      color: var(--muted);
      cursor: pointer;
      font-weight: 700;
    }}
    .danger-zone-content {{
      padding: 0 14px 14px;
      border-top: 1px solid var(--line);
    }}
    .danger-zone-content p {{
      margin: 10px 0;
    }}
    @keyframes tag-pulse {{
      0%, 100% {{ box-shadow: 0 0 0 0 color-mix(in srgb, var(--accent) 26%, transparent); }}
      50% {{ box-shadow: 0 0 0 12px transparent; }}
    }}
    .full {{ grid-column: 1 / -1; }}
    table {{ width: 100%; border-collapse: collapse; background: var(--panel); border-radius: 8px; overflow: hidden; }}
    th, td {{ padding: 10px; border-bottom: 1px solid var(--line); text-align: left; }}
    .mobile-nav {{
      display: none;
    }}
    .sr-only {{
      position: absolute;
      width: 1px;
      height: 1px;
      padding: 0;
      margin: -1px;
      overflow: hidden;
      clip: rect(0, 0, 0, 0);
      white-space: nowrap;
      border: 0;
    }}
    @media (max-width: 680px) {{
      body {{
        font-size: 15px;
        line-height: 1.32;
        padding-bottom: calc(78px + env(safe-area-inset-bottom));
      }}
      header .bar {{
        min-height: 50px;
        padding: 8px 12px;
      }}
      header nav {{
        display: none;
      }}
      main {{
        padding: 8px 12px 12px;
      }}
      footer {{
        display: none !important;
      }}
      h1 {{
        margin-top: 0;
        margin-bottom: 8px;
        line-height: 1.15;
      }}
      h2 {{
        line-height: 1.16;
      }}
      .inventory-title {{
        display: none;
      }}
      .inventory-tabs {{
        gap: 3px;
        padding: 3px;
      }}
      .inventory-tab {{
        min-height: 36px;
        padding: 5px 4px;
        font-size: .86rem;
      }}
      .inventory-tab-count {{
        min-width: 20px;
        padding: 1px 5px;
        font-size: .69rem;
      }}
      .search-row {{
        grid-template-columns: minmax(0, 1fr) auto;
        grid-column: 1 / -1;
      }}
      .expiry-add-form {{ grid-template-columns: 1fr 1fr; }}
      .expiry-add-form .btn {{ grid-column: 1 / -1; }}
      .search-row .btn {{
        min-height: 44px;
      }}
      .filter-panel {{
        display: block;
      }}
      .filter-panel summary {{
        display: inline-flex;
        align-items: center;
        justify-content: center;
        width: 42px;
        min-height: 42px;
        padding: 8px;
        border: 1px solid var(--line);
        border-radius: 10px;
        background: var(--panel);
      }}
      .filter-panel summary svg {{
        width: 21px;
        height: 21px;
        fill: none;
        stroke: currentColor;
        stroke-linecap: round;
        stroke-linejoin: round;
        stroke-width: 2;
      }}
      .filter-panel .filters {{
        margin-top: 10px;
      }}
      .toolbar {{
        grid-template-columns: auto minmax(0, 1fr);
        column-gap: 8px;
        row-gap: 7px;
        margin-bottom: 8px;
      }}
      .filter-panel {{
        grid-column: 1;
      }}
      .filter-panel[open] {{
        grid-column: 1 / -1;
      }}
      .view-switch {{
        grid-column: 2;
        justify-content: flex-end;
        gap: 6px;
        flex-wrap: nowrap;
        margin-top: 0;
      }}
      .view-switch .btn {{
        min-height: 42px;
        padding: 8px 10px;
        font-size: .86rem;
        white-space: nowrap;
      }}
      .filters {{ grid-template-columns: 1fr; }}
      .card {{
        padding: 10px;
      }}
      form.stack, .stack {{
        gap: 8px;
      }}
      .form-grid {{
        grid-template-columns: 1fr;
        gap: 8px;
      }}
      .form-card {{
        gap: 8px;
      }}
      .form-card > p {{
        margin-top: -3px;
      }}
      .form-section > summary {{
        gap: 8px;
        padding: 10px;
      }}
      .form-section-content {{
        padding: 0 10px 10px;
      }}
      .form-section-content .form-grid {{
        padding-top: 10px;
      }}
      label, .field-group {{
        gap: 4px;
      }}
      input, select, textarea {{
        padding: 9px;
      }}
      textarea {{
        min-height: 74px;
      }}
      .field-help {{
        font-size: .79rem;
        line-height: 1.25;
      }}
      .file-picker {{
        min-height: 42px;
        padding: 8px 10px;
      }}
      .barcode-step {{
        gap: 5px;
        padding: 9px;
      }}
      .barcode-scan-link {{
        min-height: 42px;
      }}
      .tag-link-card {{
        gap: 9px;
        padding: 18px 12px;
      }}
      .tag-link-icon {{
        width: 68px;
        height: 68px;
      }}
      .danger-zone > summary {{
        padding: 10px;
      }}
      .danger-zone-content {{
        padding: 0 10px 10px;
      }}
      .qty {{ font-size: 1.45rem; line-height: 1.1; }}
      .grid {{
        gap: 6px;
      }}
      .item-card {{
        grid-template-columns: 46px minmax(0, 1fr);
        gap: 8px;
        padding: 8px;
      }}
      .item-thumb {{
        width: 46px;
        border-radius: 9px;
        padding: 3px;
      }}
      .item-meta {{
        gap: 0;
        font-size: .82rem;
        line-height: 1.2;
      }}
      .item-card .item-title {{
        align-items: center;
        gap: 6px;
        margin-bottom: 2px;
      }}
      .item-card .pill {{
        min-height: 22px;
        padding: 1px 7px;
        font-size: .74rem;
        line-height: 1.1;
      }}
      .item-card .qty {{
        margin-top: 1px;
        font-size: .96rem;
        line-height: 1.15;
      }}
      .opened-count {{
        font-size: .76rem;
        line-height: 1.15;
      }}
      .item-card .actions {{
        gap: 5px;
        margin-top: 6px;
      }}
      .item-card .actions .btn {{
        min-height: 36px;
        padding: 7px 9px;
        font-size: .86rem;
      }}
      .item-card .actions .details-link {{
        margin-left: auto;
      }}
      .item-detail-card .item-hero {{
        max-height: 230px;
        margin-bottom: 9px;
        padding: 8px;
      }}
      .item-detail-card .item-title {{
        align-items: center;
        margin-bottom: 3px;
      }}
      .item-detail-card p {{
        margin: 5px 0;
      }}
      .item-detail-card .actions {{
        gap: 6px;
        margin-top: 8px;
      }}
      .item-detail-card .actions .btn {{
        min-height: 38px;
        padding: 7px 10px;
        font-size: .86rem;
      }}
      .item-row {{
        grid-template-columns: 38px minmax(0, 1fr) auto 14px;
        grid-template-rows: auto auto;
        column-gap: 8px;
        row-gap: 1px;
        padding: 6px 8px;
      }}
      .item-row-thumb {{
        grid-row: 1 / 3;
        width: 38px;
      }}
      .item-row-title {{ grid-column: 2; grid-row: 1; }}
      .item-row-meta {{ grid-column: 2 / 4; grid-row: 2; }}
      .item-row-qty {{ grid-column: 3; grid-row: 1; }}
      .item-row-arrow {{ grid-column: 4; grid-row: 1 / 3; }}
      }}
      .mobile-nav {{
        position: fixed;
        inset: auto 0 0;
        z-index: 20;
        display: grid;
        grid-template-columns: repeat(5, 1fr);
        align-items: end;
        min-height: 70px;
        padding: 7px 8px calc(7px + env(safe-area-inset-bottom));
        border-top: 1px solid var(--line);
        background: color-mix(in srgb, var(--panel) 96%, transparent);
        box-shadow: 0 -8px 28px rgb(0 0 0 / 9%);
        backdrop-filter: blur(14px);
      }}
      .mobile-nav-link {{
        display: grid;
        justify-items: center;
        gap: 3px;
        min-width: 0;
        padding: 5px 2px;
        border-radius: 12px;
        color: var(--muted);
        font-size: .69rem;
        font-weight: 750;
        line-height: 1;
        text-decoration: none;
      }}
      .mobile-nav-link svg {{
        width: 22px;
        height: 22px;
        fill: none;
        stroke: currentColor;
        stroke-linecap: round;
        stroke-linejoin: round;
        stroke-width: 2;
      }}
      .mobile-nav-link.active {{
        color: var(--accent);
      }}
      .mobile-nav-link.primary {{
        color: white;
      }}
      .mobile-nav-link.primary svg {{
        box-sizing: content-box;
        margin-top: -17px;
        padding: 13px;
        border: 5px solid var(--bg);
        border-radius: 50%;
        color: white;
        background: var(--accent);
        box-shadow: 0 6px 18px color-mix(in srgb, var(--accent) 38%, transparent);
      }}
    }}
  </style>
</head>
<body>
  <a class="skip-link" href="#main-content">Hopp til innhold</a>
  <header>
    <div class="bar">
      <div class="brand-lockup">
        <a class="brand" href=".">{APP_NAME}</a>
        <span class="app-version" aria-label="Versjon {APP_VERSION}">v{APP_VERSION}</span>
      </div>
      <nav>
        <a class="{nav_class("items")}" href=".">Varer</a>
        <a class="{nav_class("scan")}" href="scan">Scan</a>
        <a class="{nav_class("low")}" href="low-stock">Lav beholdning</a>
        <a class="{nav_class("organize")}" href="organize">Steder</a>
        <a class="{nav_class("new", True)}" href="new">Ny</a>
      </nav>
    </div>
  </header>
  <main id="main-content" tabindex="-1">{body}</main>
  <nav class="mobile-nav" aria-label="Hovedmeny">
    <a class="{mobile_nav_class("items")}" href=".">
      <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7.5h16v12H4z"/><path d="M7 4.5h10l3 3H4z"/><path d="M9 11.5h6"/></svg>
      <span>Lager</span>
    </a>
    <a class="{mobile_nav_class("scan")}" href="scan">
      <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 8V4h4M16 4h4v4M20 16v4h-4M8 20H4v-4"/><path d="M8 12h8"/></svg>
      <span>Scan</span>
    </a>
    <a class="{mobile_nav_class("new", True)}" href="new">
      <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg>
      <span>Ny</span>
    </a>
    <a class="{mobile_nav_class("low")}" href="low-stock">
      <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 5h2l2.2 9h8.9l2-6H7"/><circle cx="10" cy="19" r="1"/><circle cx="17" cy="19" r="1"/></svg>
      <span>Handleliste</span>
    </a>
    <a class="{mobile_nav_class("organize")}" href="organize">
      <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="5" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="12" cy="19" r="1"/></svg>
      <span>Mer</span>
    </a>
  </nav>
  <footer class="bar muted" style="padding-top: 24px; padding-bottom: 24px;">
    {APP_NAME} v{APP_VERSION} · Kodenavn {APP_CODENAME}
  </footer>
  <div class="save-status" role="status" aria-live="polite"></div>
  <script>
    function openNfcTagFromHomeAssistant() {{
      let topWindow;
      try {{
        topWindow = window.top;
      }} catch (error) {{
        return;
      }}
      const queryValues = new URLSearchParams(topWindow.location.search || "");
      const fragmentValues = new URLSearchParams(
        (topWindow.location.hash || "").replace(/^#/, "")
      );
      const tagId = queryValues.get("hjemmelager_tag") ||
        fragmentValues.get("hjemmelager-tag");
      if (!tagId) return;
      queryValues.delete("hjemmelager_tag");
      fragmentValues.delete("hjemmelager-tag");
      try {{
        const cleanQuery = queryValues.toString();
        const cleanFragment = fragmentValues.toString();
        topWindow.history.replaceState(
          topWindow.history.state,
          "",
          topWindow.location.pathname +
            (cleanQuery ? "?" + cleanQuery : "") +
            (cleanFragment ? "#" + cleanFragment : "")
        );
      }} catch (error) {{
        // Åpningen virker fortsatt selv om Home Assistant ikke lar oss rydde URL-en.
      }}
      window.location.replace("tag/open?tag_id=" + encodeURIComponent(tagId));
    }}

    openNfcTagFromHomeAssistant();

    function formatQuantity(value) {{
      return new Intl.NumberFormat("nb-NO", {{ maximumFractionDigits: 2 }}).format(value);
    }}

    async function handleQuickAdjustment(form, submitter) {{
      const itemContainer = form.closest("[data-item-id]");
      const quantityDisplay = itemContainer?.querySelector("[data-quantity-display]");
      const quantityValue = quantityDisplay?.querySelector("[data-quantity-value]");
      const delta = Number(form.querySelector('[name="delta"]')?.value || 0);
      const itemId = itemContainer?.dataset.itemId;
      const itemName = itemContainer?.dataset.itemName || "Varen";
      const status = document.querySelector(".save-status");
      if (!itemId || !quantityDisplay || !quantityValue || !delta) return;

      submitter.disabled = true;
      submitter.setAttribute("aria-busy", "true");
      try {{
        const response = await fetch("api/items/" + encodeURIComponent(itemId) + "/adjust", {{
          method: "POST",
          headers: {{
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"
          }},
          body: new URLSearchParams({{ delta: String(delta), note: "hurtigknapp" }})
        }});
        if (!response.ok) throw new Error("Kunne ikke oppdatere antallet");
        const payload = await response.json();
        const previous = Number(quantityDisplay.dataset.quantityRaw || 0);
        const next = Number(payload.item?.quantity || 0);
        quantityDisplay.dataset.quantityRaw = String(next);
        quantityValue.textContent = formatQuantity(next);
        const effectClass = delta > 0 ? "quantity-increased" : "quantity-decreased";
        quantityDisplay.classList.remove("quantity-increased", "quantity-decreased");
        void quantityDisplay.offsetWidth;
        quantityDisplay.classList.add(effectClass);
        window.clearTimeout(quantityDisplay.quantityFeedbackTimer);
        quantityDisplay.quantityFeedbackTimer = window.setTimeout(
          () => quantityDisplay.classList.remove(effectClass),
          2500
        );
        if (status) {{
          status.classList.remove("increased", "decreased");
          status.classList.add(delta > 0 ? "increased" : "decreased");
          status.textContent = itemName + ": " + formatQuantity(previous) + " → " + formatQuantity(next);
          window.clearTimeout(status.quickFeedbackTimer);
          status.quickFeedbackTimer = window.setTimeout(() => {{
            status.textContent = "";
            status.classList.remove("increased", "decreased");
          }}, 2600);
        }}
      }} catch (error) {{
        if (status) {{
          status.classList.remove("increased", "decreased");
          status.textContent = "Kunne ikke oppdatere antallet. Prøv igjen.";
        }}
      }} finally {{
        submitter.disabled = false;
        submitter.removeAttribute("aria-busy");
      }}
    }}

    document.addEventListener("submit", (event) => {{
      const form = event.target;
      if (!(form instanceof HTMLFormElement) || form.dataset.noBusy === "true") return;
      const submitter = event.submitter || form.querySelector('button[type="submit"], button:not([type])');
      if (!submitter) return;
      if (form.classList.contains("quick-adjust")) {{
        event.preventDefault();
        handleQuickAdjustment(form, submitter);
        return;
      }}
      window.setTimeout(() => {{
        submitter.disabled = true;
        submitter.setAttribute("aria-busy", "true");
        const status = document.querySelector(".save-status");
        if (status) status.textContent = "Lagrer …";
      }}, 0);
    }});
  </script>
</body>
</html>"""


def item_badges(item, low_label="Kjøp inn"):
    badges = []
    if item["is_low"]:
        badges.append(f'<span class="pill low">{esc(low_label)}</span>')
    if item["is_expired"]:
        badges.append('<span class="pill expired">Utløpt</span>')
    elif item["expires_soon"]:
        badges.append('<span class="pill expires-soon">Utløper snart</span>')
    return f'<span class="item-badges">{"".join(badges)}</span>' if badges else ""


def display_date(value):
    try:
        return date.fromisoformat(str(value)).strftime("%d.%m.%Y")
    except ValueError:
        return str(value or "")


def expiry_batches_panel(item):
    if item["kind"] != "consumable":
        return ""
    rows = []
    for batch in item["expiry_batches"]:
        rows.append(
            f"""
              <div class="expiry-batch-row">
                <span><strong>{fmt_num(batch['quantity'])} {esc(item['unit'])}</strong> · best før {esc(display_date(batch['best_before']))}</span>
                <form method="post" action="item/{item['id']}/expiry/clear">
                  <input type="hidden" name="best_before" value="{esc(batch['best_before'])}">
                  <button class="btn" title="Behold antallet, men fjern datoen">Fjern dato</button>
                </form>
              </div>
            """
        )
    if float(item["undated_quantity"] or 0) > 0:
        rows.append(
            f'<div class="expiry-batch-row muted"><span><strong>{fmt_num(item["undated_quantity"])} {esc(item["unit"])}</strong> uten dato</span></div>'
        )
    rows_html = "".join(rows) or '<p class="muted">Ingen beholdning har holdbarhetsdato ennå.</p>'
    return f"""
      <section class="expiry-panel">
        <h2>Holdbarhetspartier</h2>
        <p class="muted">Når du fjerner varer, brukes partiet med tidligst dato først.</p>
        <div class="expiry-batch-list">{rows_html}</div>
        <form class="expiry-add-form" method="post" action="item/{item['id']}/expiry/add">
          <label>Antall i nytt parti
            <input name="quantity" type="number" min="0.01" step="0.01" inputmode="decimal" required placeholder="For eksempel 5">
          </label>
          <label>Best før
            <input name="best_before" type="date" required>
          </label>
          <label>Antallet
            <select name="source">
              <option value="new">Legg til i totalen</option>
              <option value="existing">Finnes allerede i totalen</option>
            </select>
          </label>
          <button class="btn primary">Legg til parti</button>
        </form>
        <p class="field-help">Velg «finnes allerede» når du bare fordeler udatert beholdning på datoer.</p>
      </section>
    """


def item_card(item):
    badges = item_badges(item)
    category = item["category"] or ("Forbruksvare" if item["kind"] == "consumable" else "Gjenstand")
    location = item["location"] or "Ingen plassering"
    price = f"{fmt_price(item['price'])} kr" if fmt_price(item["price"]) else ""
    best_before = f"Best før {item['best_before']}" if item["best_before"] else ""
    extra = " · ".join(filter(None, [price, best_before]))
    quantity_label = (
        f'<span data-quantity-value>{fmt_num(item["quantity"])}</span> {esc(item["unit"])} på lager'
        if item["kind"] == "consumable"
        else f'<span data-quantity-value>{fmt_num(item["quantity"])}</span> {esc(item["unit"])}'
    )
    opened = ""
    open_action = ""
    if item["kind"] == "consumable":
        opened = f'<div class="opened-count muted">{fmt_num(item["opened_quantity"])} {esc(item["unit"])} åpne</div>'
        open_action = f'<form method="post" action="item/{item["id"]}/open"><button class="btn" title="Flytt en fra lager til åpnet">Åpne</button></form>'
    thumb = f'<a href="item/{item["id"]}"><img class="item-thumb" src="{esc(item["image_url"])}" alt=""></a>' if item["image_url"] else '<div class="item-thumb" aria-hidden="true"></div>'
    return f"""
    <article class="card item-card" data-item-id="{item['id']}" data-item-name="{esc(item['name'])}">
      {thumb}
      <div class="item-main">
        <div class="item-title">
          <h2><a class="item-name-link" href="item/{item['id']}">{esc(item['name'])}</a></h2>
          {badges}
        </div>
        <div class="item-meta muted">
          <div class="item-meta-line">{esc(category)} · {esc(location)}</div>
          {f'<div class="item-meta-line">{esc(extra)}</div>' if extra else ''}
        </div>
        <div class="qty" data-quantity-display data-quantity-raw="{float(item['quantity'])}">{quantity_label}</div>
        {opened}
        <div class="actions">
          <form class="quick-adjust" method="post" action="item/{item['id']}/adjust"><input type="hidden" name="delta" value="-1"><button class="btn" aria-label="Reduser {esc(item['name'])} med én">−</button></form>
          <form class="quick-adjust" method="post" action="item/{item['id']}/adjust"><input type="hidden" name="delta" value="1"><button class="btn primary" aria-label="Øk {esc(item['name'])} med én">+</button></form>
          {open_action}
          <a class="btn details-link" href="item/{item['id']}">Se vare</a>
        </div>
      </div>
    </article>
    """


def item_row(item):
    badges = item_badges(item)
    thumb = f'<img class="item-row-thumb" src="{esc(item["image_url"])}" alt="">' if item["image_url"] else '<div class="item-row-thumb" aria-hidden="true"></div>'
    category = item["category"] or ("Forbruksvare" if item["kind"] == "consumable" else "Gjenstand")
    location = item["location"] or "Uten plassering"
    opened = (
        f"{fmt_num(item['opened_quantity'])} åpne"
        if item["kind"] == "consumable" and float(item["opened_quantity"] or 0)
        else ""
    )
    stock_suffix = f"{esc(item['unit'])} på lager" if item["kind"] == "consumable" else esc(item["unit"])
    return f"""
    <a class="item-row" href="item/{item['id']}">
      {thumb}
      <div class="item-row-title">
        {esc(item['name'])}
        {badges}
      </div>
      <div class="item-row-meta muted">{esc(location)} · {esc(category)}</div>
      <div class="item-row-qty">{fmt_num(item['quantity'])} <span class="muted">{stock_suffix}</span>{f'<br><span class="muted">{esc(opened)}</span>' if opened else ''}</div>
      <span class="item-row-arrow" aria-hidden="true">›</span>
    </a>
    """


def option_list(values, selected, placeholder):
    options = [f'<option value="">{esc(placeholder)}</option>']
    for value in values:
        options.append(f'<option value="{esc(value)}" {"selected" if value == selected else ""}>{esc(value)}</option>')
    return "".join(options)


def query_link(params, **updates):
    next_params = dict(params)
    next_params.update(updates)
    cleaned = {key: value for key, value in next_params.items() if value}
    query = urlencode(cleaned)
    return "." + (f"?{query}" if query else "")


def inventory_empty_state(kind_view, filtered=False, clear_url="."):
    if filtered:
        return f"""
          <section class="empty-state">
            <span class="empty-state-icon" aria-hidden="true">
              <svg viewBox="0 0 24 24"><circle cx="10.5" cy="10.5" r="6.5"></circle><path d="m16 16 4 4"></path></svg>
            </span>
            <h2>Ingen treff</h2>
            <p class="muted">Prøv et annet søk, eller fjern filtrene.</p>
            <div class="empty-state-actions">
              <a class="btn primary" href="{clear_url}">Vis hele lageret</a>
              <a class="btn" href="new?kind={'thing' if kind_view == 'thing' else 'consumable'}">Legg til ny</a>
            </div>
          </section>
        """
    if kind_view == "thing":
        return """
          <section class="empty-state">
            <span class="empty-state-icon" aria-hidden="true">
              <svg viewBox="0 0 24 24"><path d="M14.5 6.5 17.5 3.5l3 3-3 3"></path><path d="m16 8-8.5 8.5a2.1 2.1 0 0 1-3-3L13 5"></path></svg>
            </span>
            <h2>Legg inn første gjenstand</h2>
            <p class="muted">For verktøy, utstyr og andre ting du vil finne igjen.</p>
            <div class="empty-state-actions">
              <a class="btn primary" href="new?kind=thing">Ny gjenstand</a>
            </div>
          </section>
        """
    if kind_view == "all":
        return """
          <section class="empty-state">
            <span class="empty-state-icon" aria-hidden="true">
              <svg viewBox="0 0 24 24"><path d="M4 8.5 12 4l8 4.5v9L12 22l-8-4.5z"></path><path d="m4 8.5 8 4.5 8-4.5M12 13v9"></path></svg>
            </span>
            <h2>Hva vil du legge inn først?</h2>
            <p class="muted">Velg den enkleste veien for det du har foran deg.</p>
            <div class="empty-state-choices">
              <a class="empty-choice" href="scan">
                <strong>Skann matvare</strong>
                <span class="muted">Hent navn og bilde fra strekkoden</span>
              </a>
              <a class="empty-choice" href="new?kind=thing">
                <strong>Legg til ting</strong>
                <span class="muted">Verktøy, utstyr og gjenstander</span>
              </a>
            </div>
          </section>
        """
    return """
      <section class="empty-state">
        <span class="empty-state-icon" aria-hidden="true">
          <svg viewBox="0 0 24 24"><path d="M4 7h16v10H4zM7 4v3M17 4v3M8 11h8M8 14h5"></path></svg>
        </span>
        <h2>Legg inn første forbruksvare</h2>
        <p class="muted">Skann strekkoden for å hente navn og bilde automatisk.</p>
        <div class="empty-state-actions">
          <a class="btn primary" href="scan">Skann strekkode</a>
          <a class="btn" href="new?kind=consumable">Legg inn manuelt</a>
        </div>
      </section>
    """


def new_item_start_page():
    return """
      <section class="empty-state new-start">
        <span class="empty-state-icon" aria-hidden="true">
          <svg viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"></path></svg>
        </span>
        <h1>Hva vil du legge til?</h1>
        <p class="muted">Velg den raskeste veien. Du kan fylle inn flere detaljer senere.</p>
        <div class="empty-state-choices">
          <a class="empty-choice" href="scan">
            <span class="new-choice-icon" aria-hidden="true">
              <svg viewBox="0 0 24 24"><path d="M4 8V4h4M16 4h4v4M20 16v4h-4M8 20H4v-4"></path><path d="M8 9v6M11 9v6M14 9v6M17 9v6"></path></svg>
            </span>
            <span class="new-choice-copy">
              <strong>Skann en vare</strong>
              <span class="muted">Hent navn og bilde fra strekkoden</span>
            </span>
          </a>
          <a class="empty-choice" href="new?kind=consumable">
            <span class="new-choice-icon" aria-hidden="true">
              <svg viewBox="0 0 24 24"><path d="M5 7h14v12H5zM8 4h8v3M8 11h8M8 15h5"></path></svg>
            </span>
            <span class="new-choice-copy">
              <strong>Skriv inn en vare</strong>
              <span class="muted">Mat, husholdning og andre forbruksvarer</span>
            </span>
          </a>
          <a class="empty-choice" href="new?kind=thing">
            <span class="new-choice-icon" aria-hidden="true">
              <svg viewBox="0 0 24 24"><path d="M14.5 6.5 17.5 3.5l3 3-3 3"></path><path d="m16 8-8.5 8.5a2.1 2.1 0 0 1-3-3L13 5"></path></svg>
            </span>
            <span class="new-choice-copy">
              <strong>Legg inn en gjenstand</strong>
              <span class="muted">Verktøy, utstyr og ting du vil finne igjen</span>
            </span>
          </a>
        </div>
      </section>
    """


def item_form(item=None, tag_id="", barcode="", kind="consumable"):
    is_new = item is None
    kind = kind if kind in ("consumable", "thing") else "consumable"
    item = item or {
        "id": None,
        "name": "",
        "kind": kind,
        "quantity": 1,
        "opened_quantity": 0,
        "unit": "stk",
        "min_quantity": 0,
        "target_quantity": 0,
        "price": 0,
        "best_before": "",
        "location": "",
        "category": "",
        "tag_id": tag_id,
        "barcode": barcode,
        "image_url": "",
        "note": "",
        "shopping_enabled": 1,
    }
    is_thing = item["kind"] == "thing"
    noun = "gjenstanden" if is_thing else "varen"
    example = "For eksempel Slagdrill" if is_thing else "For eksempel Havregryn"
    save_label = "Lagre gjenstand" if is_thing else "Lagre vare"
    action = f"item/{item['id']}/edit" if item["id"] else "new"
    checked = "checked" if item["shopping_enabled"] else ""
    image_url = "" if str(item["image_url"]).startswith("data:") else item["image_url"]
    preview_src = item["image_url"] or ""
    preview_hidden = "" if preview_src else "hidden"
    categories = distinct_values("category")
    locations = distinct_values("location")
    barcode_step = ""
    if is_new and not is_thing:
        if item["barcode"]:
            barcode_step = f"""
              <div class="full barcode-step" id="barcode-step">
                <div class="barcode-confirmation">
                  <span><strong>Strekkode lest</strong><br><span class="muted">{esc(item["barcode"])}</span></span>
                  <a class="btn" href="scan">Skann på nytt</a>
                </div>
                <div class="product-suggestion" id="product-suggestion">
                  <div class="product-suggestion-image" id="product-suggestion-placeholder" aria-hidden="true"></div>
                  <div class="product-suggestion-copy">
                    <strong id="product-suggestion-title">Slår opp produkt …</strong>
                    <p class="muted" id="product-suggestion-detail">Du kan fylle inn feltene mens vi søker.</p>
                    <p class="product-source" id="product-suggestion-source"></p>
                  </div>
                </div>
              </div>
            """
        else:
            barcode_step = """
              <div class="full barcode-step" id="barcode-step">
                <a class="btn primary barcode-scan-link" href="scan">
                  <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 8V4h4M16 4h4v4M20 16v4h-4M8 20H4v-4"/><path d="M8 9v6M11 9v6M14 9v6M17 9v6"/></svg>
                  Skann strekkode
                </a>
                <span class="field-help">Raskeste vei for matvarer og andre produkter med strekkode.</span>
              </div>
            """
    remove_image = f"""
        <label class="full">
          <span><input type="checkbox" name="remove_image" value="1"> Fjern bilde</span>
        </label>
    """ if item["image_url"] else ""
    image_section = f"""
      <details class="card form-section">
        <summary>
          <span class="form-section-summary">
            Bilde
            <small>Valgfritt – velg fra telefonen eller bruk kameraet</small>
          </span>
        </summary>
        <div class="form-section-content">
          <div class="form-grid">
            <div class="full field-group">
              <label class="file-picker" for="item-image-file">
                <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h3l1.5-2h7L17 7h3v12H4z"/><circle cx="12" cy="13" r="3"/></svg>
                Velg eller ta bilde
              </label>
              <input class="sr-only" id="item-image-file" name="image_file" type="file" accept="image/*">
              <input id="item-image-data" name="image_file_data_url" type="hidden">
              <div class="item-image-preview" id="item-image-preview" {preview_hidden}>
                <img id="item-image-preview-img" src="{esc(preview_src)}" alt="">
                <div>
                  <strong id="item-image-preview-title">{"Nåværende bilde" if preview_src else "Bilde valgt"}</strong>
                  <span class="field-help" id="item-image-preview-status">{"Velg et nytt bilde for å bytte." if preview_src else ""}</span>
                </div>
              </div>
              <span class="field-help">Store bilder gjøres mindre automatisk.</span>
            </div>
            {remove_image}
          </div>
        </div>
      </details>
    """
    kind_field = (
        f'<input type="hidden" name="kind" value="{esc(item["kind"])}">'
        if is_new
        else f"""
          <label>Type
            <select name="kind" id="item-kind">
              <option value="consumable" {"selected" if item['kind'] == 'consumable' else ""}>Forbruk</option>
              <option value="thing" {"selected" if item['kind'] == 'thing' else ""}>Ting</option>
            </select>
          </label>
        """
    )
    location_field = (
        ""
        if is_new
        else f"""
          <label class="full">Plassering
            <select name="location">{option_list(locations, item["location"], "Velg senere")}</select>
          </label>
        """
    )
    advanced_location_field = (
        f"""
          <label class="full">Plassering
            <select name="location">{option_list(locations, item["location"], "Velg senere")}</select>
          </label>
        """
        if is_new
        else ""
    )
    expiry_field = (
        f"""
          <label class="full">Holdbarhetsdato
            <input name="best_before" type="date" value="{esc(item['best_before'])}">
            <span class="field-help">Hele startantallet får denne datoen. Flere partier kan legges til fra varesiden.</span>
          </label>
        """
        if is_new
        else """
          <div class="full field-help">
            Holdbarhetsdatoer og partier administreres fra varesiden.
          </div>
        """
    )
    return f"""
    <form class="stack" method="post" action="{action}" enctype="multipart/form-data">
      <section class="card form-card">
        <h2>Det viktigste</h2>
        <p class="muted">Navn er nok. Alt annet kan legges til senere.</p>
        <div class="form-grid">
          {barcode_step}
          {kind_field}
          <label class="full">Hva heter {noun}?
            <input id="item-name" name="name" value="{esc(item['name'])}" placeholder="{example}" required autofocus>
          </label>
          <label>Antall
            <input name="quantity" type="number" step="0.01" value="{fmt_num(item['quantity'])}" inputmode="decimal">
          </label>
          {location_field}
        </div>
      </section>

      {image_section}

      <details class="card form-section" {"hidden" if is_thing else ""}>
        <summary>
          <span class="form-section-summary">
            Lager og handleliste
            <small>Enhet, minimum, pris og holdbarhet</small>
          </span>
        </summary>
        <div class="form-section-content">
          <div class="form-grid">
            <label>Enhet
              <input id="item-unit" name="unit" value="{esc(item['unit'])}" placeholder="stk, pk, meter">
            </label>
            <label>Åpne pakker
              <input name="opened_quantity" type="number" step="0.01" value="{fmt_num(item['opened_quantity'])}">
            </label>
            <label>Varsle ved antall
              <input name="min_quantity" type="number" step="0.01" value="{fmt_num(item['min_quantity'])}">
            </label>
            <label>Fyll opp til
              <input name="target_quantity" type="number" step="0.01"
                     value="{fmt_num(item['target_quantity']) if float(item['target_quantity'] or 0) > 0 else ''}"
                     placeholder="Bruker varslingsgrensen">
              <span class="field-help">Handlelisten foreslår å kjøpe opp til dette antallet.</span>
            </label>
            <label>Pris
              <input name="price" type="number" step="0.01" value="{esc(fmt_price(item['price']))}" placeholder="Valgfritt">
            </label>
            {expiry_field}
            <label class="full">
              <input type="hidden" name="shopping_enabled" value="0">
              <span><input type="checkbox" name="shopping_enabled" value="1" {checked}> Legg på handlelisten når beholdningen blir lav</span>
            </label>
          </div>
        </div>
      </details>

      <details class="card form-section">
        <summary>
          <span class="form-section-summary">
            Plassering og kategori
            <small>Organiser varen mer detaljert</small>
          </span>
        </summary>
        <div class="form-section-content">
          <div class="form-grid">
            {advanced_location_field}
            <label class="full">Legg til ny plassering
              <input name="new_location" placeholder="For eksempel Kjøkken › Skap">
            </label>
            <label>Kategori
              <select id="item-category" name="category">{option_list(categories, item["category"], "Ingen kategori")}</select>
            </label>
            <label>Legg til ny kategori
              <input id="item-new-category" name="new_category" placeholder="Matvarer, verktøy …">
            </label>
          </div>
        </div>
      </details>

      <details class="card form-section">
        <summary>
          <span class="form-section-summary">
            Koder og NFC
            <small>Helt valgfritt</small>
          </span>
        </summary>
        <div class="form-section-content">
          <div class="form-grid">
            <label class="full">Strekkode eller QR-kode
              <input name="barcode" value="{esc(item['barcode'] or '')}" placeholder="Kan legges til via Scan">
            </label>
            <label class="full">Home Assistant Tag-ID
              <input name="tag_id" value="{esc(item['tag_id'] or '')}" placeholder="Valgfritt">
              <span class="field-help">Bruk helst «Koble NFC-tag» på varesiden. Feltet er kun for manuell reservebruk.</span>
            </label>
            <label class="full">Bilde-URL
              <input id="item-image-url" name="image_url" value="{esc(image_url)}" placeholder="Kun hvis bildet ligger på nett">
            </label>
          </div>
        </div>
      </details>

      <details class="card form-section">
        <summary>
          <span class="form-section-summary">
            Notat
            <small>Tilleggsinformasjon om varen</small>
          </span>
        </summary>
        <div class="form-section-content">
          <div class="form-grid">
            <label class="full">Notat
              <textarea name="note">{esc(item['note'])}</textarea>
            </label>
          </div>
        </div>
      </details>

      <div class="actions">
        <button class="btn primary">{save_label}</button>
        <a class="btn" href=".">Avbryt</a>
      </div>
    </form>
    <script>
      const kindSelect = document.getElementById("item-kind");
      const barcodeStep = document.getElementById("barcode-step");
      function updateBarcodeStep() {{
        if (barcodeStep && kindSelect) {{
          barcodeStep.hidden = kindSelect.value !== "consumable";
        }}
      }}
      kindSelect?.addEventListener("change", updateBarcodeStep);
      updateBarcodeStep();

      const imageInput = document.getElementById("item-image-file");
      const imageDataInput = document.getElementById("item-image-data");
      const imagePreview = document.getElementById("item-image-preview");
      const imagePreviewImg = document.getElementById("item-image-preview-img");
      const imagePreviewTitle = document.getElementById("item-image-preview-title");
      const imagePreviewStatus = document.getElementById("item-image-preview-status");
      imageInput?.addEventListener("change", async () => {{
        const file = imageInput.files?.[0];
        if (!file) return;
        imagePreview.hidden = false;
        imagePreviewTitle.textContent = file.name || "Bilde valgt";
        imagePreviewStatus.textContent = "Klargjør bilde …";
        const objectUrl = URL.createObjectURL(file);
        imagePreviewImg.src = objectUrl;
        try {{
          const source = new Image();
          await new Promise((resolve, reject) => {{
            source.onload = resolve;
            source.onerror = reject;
            source.src = objectUrl;
          }});
          const maxSide = 1400;
          const scale = Math.min(1, maxSide / Math.max(source.naturalWidth, source.naturalHeight));
          const canvas = document.createElement("canvas");
          canvas.width = Math.max(1, Math.round(source.naturalWidth * scale));
          canvas.height = Math.max(1, Math.round(source.naturalHeight * scale));
          const context = canvas.getContext("2d");
          context.fillStyle = "#fff";
          context.fillRect(0, 0, canvas.width, canvas.height);
          context.drawImage(source, 0, 0, canvas.width, canvas.height);
          let dataUrl = canvas.toDataURL("image/jpeg", .82);
          if (dataUrl.length > 2400000) {{
            dataUrl = canvas.toDataURL("image/jpeg", .62);
          }}
          imageDataInput.value = dataUrl;
          imagePreviewImg.src = dataUrl;
          imageInput.value = "";
          const sizeKb = Math.round((dataUrl.length * 3 / 4) / 1024);
          imagePreviewStatus.textContent = `Bilde klart · ca. ${{sizeKb}} kB`;
        }} catch (error) {{
          imageDataInput.value = "";
          imagePreviewStatus.textContent = "Originalbildet sendes. Store bilder kan bli avvist.";
        }} finally {{
          URL.revokeObjectURL(objectUrl);
        }}
      }});

      const lookupBarcode = {json.dumps(item["barcode"] if is_new else "")};
      const suggestion = document.getElementById("product-suggestion");
      async function lookupProduct() {{
        if (!lookupBarcode || !suggestion) return;
        const title = document.getElementById("product-suggestion-title");
        const detail = document.getElementById("product-suggestion-detail");
        const source = document.getElementById("product-suggestion-source");
        try {{
          const response = await fetch(
            "api/product-lookup?barcode=" + encodeURIComponent(lookupBarcode),
            {{ headers: {{ "Accept": "application/json" }}, cache: "no-store" }}
          );
          const product = await response.json();
          if (product.status !== "found") {{
            title.textContent = "Fyll inn produktet manuelt";
            detail.textContent = product.message || "Fant ikke produktet.";
            return;
          }}

          const nameInput = document.getElementById("item-name");
          const unitInput = document.getElementById("item-unit");
          const categorySelect = document.getElementById("item-category");
          const newCategoryInput = document.getElementById("item-new-category");
          const imageUrlInput = document.getElementById("item-image-url");
          if (!nameInput.value.trim()) nameInput.value = product.name || "";
          if (unitInput && unitInput.value.trim() === "stk") {{
            unitInput.value = product.suggested_unit || "pk";
          }}
          if (categorySelect && product.suggested_category) {{
            const matchingOption = [...categorySelect.options]
              .find((option) => option.value === product.suggested_category);
            if (matchingOption) {{
              categorySelect.value = matchingOption.value;
            }} else if (newCategoryInput && !newCategoryInput.value.trim()) {{
              newCategoryInput.value = product.suggested_category;
            }}
          }}
          if (imageUrlInput && product.image_data && !imageUrlInput.value) {{
            imageUrlInput.value = product.image_data;
            const oldPreview = document.getElementById("product-suggestion-placeholder");
            const preview = document.createElement("img");
            preview.id = "product-suggestion-placeholder";
            preview.className = "product-suggestion-image";
            preview.src = product.image_data;
            preview.alt = "";
            oldPreview?.replaceWith(preview);
          }}

          title.textContent = product.name;
          const productFacts = [product.brand, product.package_size].filter(Boolean).join(" · ");
          const filled = ["navn", "enhet", "kategori"];
          if (product.image_data) filled.push("bilde");
          detail.textContent =
            (productFacts ? productFacts + ". " : "") +
            "Vi fylte inn " + filled.join(", ") + ". Kontroller og lagre.";
          source.innerHTML =
            'Produktdata fra <a href="' + product.source_url +
            '" target="_blank" rel="noopener">Open Food Facts</a>';
        }} catch (error) {{
          title.textContent = "Fyll inn produktet manuelt";
          detail.textContent = "Produktoppslaget er ikke tilgjengelig akkurat nå.";
        }}
      }}
      lookupProduct();
    </script>
    """


def tag_link_page(
    item,
    session,
    route_base=None,
    status_url=None,
    direct_url=None,
    back_url=None,
    target_description=None,
):
    route_base = route_base or f"item/{item['id']}/tag-link"
    status_url = status_url or f"api/tag-link/status?item_id={item['id']}"
    direct_url = direct_url or f"item/{item['id']}/tag-open-setup"
    back_url = back_url or f"item/{item['id']}"
    target_description = target_description or f'«{item["name"]}»'
    nfc_connection = get_home_assistant_nfc_state()
    status = session["status"] if session else "cancelled"
    messages = {
        "waiting": f'Åpne Home Assistant-appen og skann klistremerket du vil bruke på {target_description}.',
        "linked": session["message"] if session else "Taggen er koblet.",
        "conflict": session["message"] if session else "Taggen er allerede i bruk.",
        "expired": session["message"] if session else "Tiden løp ut.",
        "cancelled": session["message"] if session else "Koblingen er ikke aktiv.",
    }
    waiting = status == "waiting"
    icon_class = "tag-link-icon waiting" if waiting else "tag-link-icon"
    status_text = messages.get(status, "Koblingen er ikke aktiv.")
    countdown = (
        f'<span id="tag-link-countdown">{session["seconds_left"]}</span> sekunder igjen'
        if waiting
        else ""
    )
    retry = (
        f"""
          <form method="post" action="{esc(route_base)}/start">
            <button class="btn primary">Prøv igjen</button>
          </form>
        """
        if status in ("conflict", "expired", "cancelled")
        else ""
    )
    cancel = (
        f"""
          <form method="post" action="{esc(route_base)}/cancel">
            <button class="btn">Avbryt</button>
          </form>
        """
        if waiting
        else ""
    )
    done = (
        (
            f'<a class="btn primary" href="{esc(direct_url)}">'
            "Gjør taggen klar for direkte åpning</a>"
        )
        if status == "linked"
        else ""
    )
    return f"""
      <section class="card tag-link-card" data-status="{esc(status)}">
        <div class="{icon_class}" id="tag-link-icon" aria-hidden="true">
          <svg viewBox="0 0 24 24">
            <path d="M7.5 8.5a5 5 0 0 1 0 7M10.5 6a8.5 8.5 0 0 1 0 12"/>
            <path d="M14 9.5v5M17 7v10M20 5v14"/>
          </svg>
        </div>
        <h1 id="tag-link-title">{"Venter på NFC-tag" if waiting else "Koble NFC-tag"}</h1>
        <p class="tag-link-status" id="tag-link-message">{esc(status_text)}</p>
        <div class="nfc-connection" id="nfc-connection" data-state="{esc(nfc_connection["status"])}">
          {esc(nfc_connection["message"])}
        </div>
        <p class="muted" id="tag-link-countdown-wrap">{countdown}</p>
        <div class="actions" id="tag-link-actions">
          {cancel}
          {retry}
          {done}
        </div>
      </section>
      <script>
        const tagLinkRoute = {json.dumps(route_base, ensure_ascii=False)};
        const tagLinkStatusUrl = {json.dumps(status_url, ensure_ascii=False)};
        const tagLinkDirectUrl = {json.dumps(direct_url, ensure_ascii=False)};
        const tagLinkBackUrl = {json.dumps(back_url, ensure_ascii=False)};
        const initialStatus = {status!r};
        const statusTitle = document.getElementById("tag-link-title");
        const statusMessage = document.getElementById("tag-link-message");
        const countdownWrap = document.getElementById("tag-link-countdown-wrap");
        const actions = document.getElementById("tag-link-actions");
        const icon = document.getElementById("tag-link-icon");
        const nfcConnection = document.getElementById("nfc-connection");

        function showResult(data) {{
          if (data.home_assistant && nfcConnection) {{
            nfcConnection.dataset.state = data.home_assistant.status || "connecting";
            nfcConnection.textContent = data.home_assistant.message || "Kobler til Home Assistant …";
          }}
          if (data.status === "waiting") {{
            const seconds = Math.max(0, Number(data.seconds_left || 0));
            countdownWrap.textContent = seconds + " sekunder igjen";
            return;
          }}
          icon.classList.remove("waiting");
          countdownWrap.textContent = "";
          statusMessage.textContent = data.message || "";
          if (data.status === "linked") {{
            statusTitle.textContent = "Taggen er koblet ✓";
            actions.innerHTML =
              '<a class="btn primary" href="' + tagLinkDirectUrl +
              '">Gjør taggen klar for direkte åpning</a>';
          }} else if (data.status === "conflict") {{
            statusTitle.textContent = "Taggen er allerede i bruk";
            actions.innerHTML =
              '<form method="post" action="' + tagLinkRoute + '/start">' +
              '<button class="btn primary">Prøv igjen</button></form>' +
              '<a class="btn" href="' + tagLinkBackUrl + '">Avslutt</a>';
          }} else {{
            statusTitle.textContent = "Koblingen ble ikke fullført";
            actions.innerHTML =
              '<form method="post" action="' + tagLinkRoute + '/start">' +
              '<button class="btn primary">Prøv igjen</button></form>' +
              '<a class="btn" href="' + tagLinkBackUrl + '">Avslutt</a>';
          }}
          clearInterval(pollTimer);
        }}

        async function pollStatus() {{
          try {{
            const response = await fetch(tagLinkStatusUrl, {{
              headers: {{ "Accept": "application/json" }},
              cache: "no-store"
            }});
            if (response.ok) showResult(await response.json());
          }} catch (error) {{
            statusMessage.textContent = "Mistet forbindelsen. Prøver igjen …";
          }}
        }}

        let pollTimer = null;
        if (initialStatus === "waiting") {{
          pollTimer = setInterval(pollStatus, 1000);
          pollStatus();
        }}
      </script>
    """


def tag_open_setup_page(
    item,
    addon_slug=None,
    back_url=None,
    link_url=None,
    heading=None,
    description=None,
):
    back_url = back_url or f"item/{item['id']}"
    link_url = link_url or f"item/{item['id']}/tag-link"
    heading = heading or f'Åpne «{item["name"]}» fra NFC'
    description = description or (
        "Dette erstatter den vanlige Home Assistant-lenken på taggen med en "
        "lenke som åpner akkurat denne varen."
    )
    links = direct_nfc_links(item.get("tag_id"), addon_slug or get_addon_slug())
    if not item.get("tag_id"):
        return f"""
          <section class="card stack">
            <h1>Taggen er ikke koblet</h1>
            <p>Koble en NFC-tag før du gjør den klar for direkte åpning.</p>
            <a class="btn primary" href="{esc(link_url)}">Koble NFC-tag</a>
          </section>
        """
    if not links["android"]:
        return f"""
          <section class="card stack">
            <h1>Direkte åpning er ikke tilgjengelig ennå</h1>
            <p>Hjemmelager fant ikke adressen til panelet i Home Assistant.</p>
            <p class="muted">Start add-onen på nytt og åpne denne siden gjennom Home Assistant.</p>
            <a class="btn" href="{esc(back_url)}">Tilbake</a>
          </section>
        """

    android_url = esc(links["android"])
    iphone_url = esc(links["iphone"])
    tag_id_json = json.dumps(str(item.get("tag_id") or ""), ensure_ascii=False)
    return f"""
      <section class="stack">
        <div class="page-heading">
          <div>
            <h1>{esc(heading)}</h1>
            <p class="muted">{esc(description)}</p>
          </div>
          <a class="btn" href="{esc(back_url)}">Tilbake</a>
        </div>
        <div class="card stack">
          <h2>Android</h2>
          <p>Trykk knappen og hold telefonen mot NFC-taggen. Dette skriver en ny lenke på taggen; koblingen til varen i Hjemmelager beholdes.</p>
          <div class="actions">
            <button class="btn primary" id="write-android-tag" type="button">Skriv taggen</button>
            <button class="btn" id="copy-android-url" type="button" data-copy-url="{android_url}">Kopier Android-lenken</button>
            <a class="btn" id="test-android-url" href="{android_url}">Test i Home Assistant</a>
          </div>
          <p class="muted" id="nfc-write-status" role="status"></p>
        </div>
        <div class="card stack">
          <h2>iPhone</h2>
          <p>Home Assistant-appen kan koble taggen, men kan ikke skrive denne direkteåpningslenken. Kopier iPhone-lenken og skriv den som en URL med en NFC-skriverapp. Når NFC-varselet vises, trykker du «Åpne i Home Assistant».</p>
          <div class="actions">
            <button class="btn primary" id="copy-iphone-url" type="button" data-copy-url="{iphone_url}">Kopier iPhone-lenken</button>
            <a class="btn" id="test-iphone-url" href="{android_url}">Test i Home Assistant</a>
          </div>
          <p class="muted">«Test i Home Assistant» tester selve appåpningen. iPhone-lenken over er kun laget for å ligge på NFC-taggen, og skal ikke åpnes i nettleseren.</p>
        </div>
        <p class="muted" id="nfc-panel-path" role="status"></p>
      </section>
      <script>
        const directTagId = {tag_id_json};
        let androidNfcUrl = {links["android"]!r};
        let iphoneNfcUrl = {links["iphone"]!r};
        const writeButton = document.getElementById("write-android-tag");
        const writeStatus = document.getElementById("nfc-write-status");
        const panelPathStatus = document.getElementById("nfc-panel-path");

        function useCurrentHomeAssistantPanelPath() {{
          try {{
            const panelPath = window.top.location.pathname || "";
            if (!panelPath || panelPath.startsWith("/api/hassio_ingress/")) return;
            androidNfcUrl = "homeassistant://navigate" + panelPath +
              "?server=default#hjemmelager-tag=" + encodeURIComponent(directTagId);
            iphoneNfcUrl = "https://www.home-assistant.io/ios/nfc/?url=" +
              encodeURIComponent(androidNfcUrl);
            document.getElementById("copy-android-url").dataset.copyUrl = androidNfcUrl;
            document.getElementById("copy-iphone-url").dataset.copyUrl = iphoneNfcUrl;
            document.getElementById("test-android-url").href = androidNfcUrl;
            document.getElementById("test-iphone-url").href = androidNfcUrl;
            panelPathStatus.textContent =
              "Direktelenken bruker Home Assistant-stien " + panelPath + ".";
          }} catch (error) {{
            panelPathStatus.textContent =
              "Kunne ikke lese panelstien. Lenken bruker add-on-adressen som reserve.";
          }}
        }}

        useCurrentHomeAssistantPanelPath();

        async function copyUrl(value, button) {{
          try {{
            await navigator.clipboard.writeText(value);
            const oldText = button.textContent;
            button.textContent = "Kopiert ✓";
            window.setTimeout(() => button.textContent = oldText, 1800);
          }} catch (error) {{
            window.prompt("Kopier lenken:", value);
          }}
        }}

        document.querySelectorAll("[data-copy-url]").forEach((button) => {{
          button.addEventListener("click", () => copyUrl(button.dataset.copyUrl, button));
        }});

        if (!("NDEFReader" in window)) {{
          writeButton.disabled = true;
          writeStatus.textContent =
            "Direkte skriving støttes ikke i denne nettleseren. Bruk «Kopier lenken» i en NFC-skriverapp.";
        }} else {{
          writeButton.addEventListener("click", async () => {{
            writeButton.disabled = true;
            writeStatus.textContent = "Hold telefonen inntil NFC-taggen …";
            try {{
              const writer = new NDEFReader();
              await writer.write({{
                records: [{{ recordType: "url", data: androidNfcUrl }}]
              }});
              writeStatus.textContent = "Taggen er skrevet ✓ Du kan teste den nå.";
            }} catch (error) {{
              writeStatus.textContent =
                "Kunne ikke skrive taggen. Prøv igjen, eller kopier lenken til en NFC-skriverapp.";
            }} finally {{
              writeButton.disabled = false;
            }}
          }});
        }}
      </script>
    """


def location_tag_link_page(location, session):
    encoded_location = quote(location, safe="")
    return tag_link_page(
        {"id": 0, "name": location},
        session,
        route_base=f"location/{encoded_location}/tag-link",
        status_url="api/location-tag-link/status?" + urlencode({"location": location}),
        direct_url=f"location/{encoded_location}/tag-open-setup",
        back_url="organize",
        target_description=f'plasseringen «{location}»',
    )


def location_tag_open_setup_page(location, addon_slug=None):
    location_tag = get_location_tag(location) or {}
    encoded_location = quote(location, safe="")
    return tag_open_setup_page(
        {
            "id": 0,
            "name": location,
            "tag_id": location_tag.get("tag_id"),
        },
        addon_slug=addon_slug,
        back_url="organize",
        link_url=f"location/{encoded_location}/tag-link",
        heading=f'Åpne plasseringen «{location}» fra NFC',
        description=(
            "Taggen åpner lageret ferdig filtrert til denne plasseringen. "
            "Produkttagger fortsetter å åpne den enkelte varen."
        ),
    )


def scan_page():
    return """
    <section class="stack">
      <h1>Skann kode</h1>
      <div class="card stack">
        <video id="scanner-video" class="scanner" playsinline muted></video>
        <div class="actions">
          <button id="start-scan" class="btn primary" type="button">Skann med kamera</button>
          <button id="stop-scan" class="btn" type="button">Stopp</button>
        </div>
        <p id="scan-status" class="muted">Trykk «Skann med kamera» og hold strekkoden rolig i bildet.</p>
        <details class="scanner-diagnostics-wrap">
          <summary>Feilsøking</summary>
          <dl id="scanner-diagnostics" class="scanner-diagnostics" aria-live="polite"></dl>
        </details>
        <form class="stack" method="get" action="scan/result">
          <label>Manuell kode
            <input name="code" autocomplete="off" inputmode="text" placeholder="Lim inn eller skriv strekkode/QR-kode">
          </label>
          <button class="btn">Søk kode</button>
        </form>
      </div>
    </section>
    <script src="static/zxing-browser.min.js"></script>
    <script>
      const video = document.getElementById('scanner-video');
      const statusEl = document.getElementById('scan-status');
      const startBtn = document.getElementById('start-scan');
      const stopBtn = document.getElementById('stop-scan');
      let codeReader = null;
      let scannerControls = null;
      let hasScanned = false;
      const diagnosticsEl = document.getElementById('scanner-diagnostics');
      const diagnostics = {
        secureContext: window.isSecureContext,
        mediaDevices: !!navigator.mediaDevices,
        getUserMedia: !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia),
        zxingLoaded: !!window.ZXingBrowser,
        videoInputCount: 0,
        selectedCamera: 'Ikke valgt',
        selectedDeviceId: '',
        lastError: 'Ingen'
      };
      const diagnosticLabels = {
        secureContext: 'window.isSecureContext',
        mediaDevices: 'navigator.mediaDevices',
        getUserMedia: 'navigator.mediaDevices.getUserMedia',
        zxingLoaded: 'ZXingBrowser loaded',
        videoInputCount: 'Video input devices',
        selectedCamera: 'Valgt kamera',
        selectedDeviceId: 'Valgt deviceId',
        lastError: 'Siste feil'
      };

      function setStatus(text) {
        statusEl.textContent = text;
      }

      function setLastError(message) {
        diagnostics.lastError = message || 'Ingen';
        renderDiagnostics();
      }

      function formatDiagnosticValue(value) {
        if (typeof value === 'boolean') return value ? 'ja' : 'nei';
        return value || '-';
      }

      function renderDiagnostics() {
        diagnostics.zxingLoaded = !!window.ZXingBrowser;
        diagnosticsEl.replaceChildren();
        for (const key of Object.keys(diagnosticLabels)) {
          const term = document.createElement('dt');
          const detail = document.createElement('dd');
          term.textContent = diagnosticLabels[key];
          detail.textContent = formatDiagnosticValue(diagnostics[key]);
          diagnosticsEl.append(term, detail);
        }
      }

      function stopScan() {
        if (scannerControls) {
          scannerControls.stop();
          scannerControls = null;
        }
        if (video.srcObject) {
          for (const track of video.srcObject.getTracks()) {
            track.stop();
          }
        }
        video.srcObject = null;
      }

      function openCode(rawCode) {
        const code = (rawCode || '').trim();
        if (!code) return;
        if (hasScanned) return;
        hasScanned = true;
        setStatus('Kode lest');
        stopScan();
        window.location.href = 'scan/result?code=' + encodeURIComponent(code);
      }

      async function requestCameraPermission() {
        const stream = await navigator.mediaDevices.getUserMedia({
          video: { facingMode: { ideal: 'environment' } },
          audio: false
        });
        for (const track of stream.getTracks()) {
          track.stop();
        }
      }

      async function getVideoInputs() {
        let devices = await navigator.mediaDevices.enumerateDevices();
        let videoInputs = devices.filter((device) => device.kind === 'videoinput');
        if (videoInputs.length && videoInputs.every((device) => !device.label)) {
          await requestCameraPermission();
          devices = await navigator.mediaDevices.enumerateDevices();
          videoInputs = devices.filter((device) => device.kind === 'videoinput');
        }
        diagnostics.videoInputCount = videoInputs.length;
        renderDiagnostics();
        return videoInputs;
      }

      function chooseCamera(videoInputs) {
        const rearWords = ['back', 'rear', 'environment', 'bak'];
        const rearCamera = videoInputs.find((device) => {
          const label = (device.label || '').toLowerCase();
          return rearWords.some((word) => label.includes(word));
        });
        const selected = rearCamera || videoInputs[videoInputs.length - 1] || null;
        diagnostics.selectedCamera = selected ? (selected.label || 'Uten kameranavn') : 'Automatisk';
        diagnostics.selectedDeviceId = selected ? selected.deviceId : '';
        renderDiagnostics();
        return selected ? selected.deviceId : null;
      }

      async function startScan() {
        try {
          if (!window.ZXingBrowser) {
            throw new Error('ZXing-biblioteket ble ikke lastet');
          }
          if (!window.isSecureContext) {
            throw new Error('Kamera krever sikker tilkobling/HTTPS');
          }
          if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
            throw new Error('Kamera krever sikker tilkobling/HTTPS');
          }
          stopScan();
          hasScanned = false;
          setLastError('Ingen');
          const videoInputs = await getVideoInputs();
          if (!videoInputs.length) {
            throw new Error('Fant ingen kameraenheter');
          }
          const deviceId = chooseCamera(videoInputs);
          codeReader = codeReader || new ZXingBrowser.BrowserMultiFormatReader();
          setStatus('Kamera startet – hold strekkoden rolig i bildet');
          scannerControls = await codeReader.decodeFromVideoDevice(
            deviceId,
            video,
            (result, err, controls) => {
              scannerControls = controls;
              if (result && !hasScanned) {
                if (controls) {
                  controls.stop();
                  scannerControls = null;
                }
                openCode(result.getText());
              } else if (err) {
                console.debug('ZXing decode:', err);
              }
            }
          );
        } catch (err) {
          const message = err.message || 'Kunne ikke starte kamera.';
          setLastError(message);
          setStatus(message);
          stopScan();
        }
      }

      startBtn.addEventListener('click', startScan);
      stopBtn.addEventListener('click', stopScan);
      renderDiagnostics();
      if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
        setStatus('Kamera krever en sikker HTTPS-tilkobling. Du kan fortsatt skrive inn koden manuelt.');
      }
    </script>
    """


def shopping_list_page():
    items = list_items(
        "kind = 'consumable' and shopping_enabled = 1 and min_quantity > 0 and quantity <= min_quantity"
    )
    remaining = [item for item in items if not item["shopping_checked"]]
    completed = [item for item in items if item["shopping_checked"]]

    def shopping_row(item):
        checked = bool(item["shopping_checked"])
        target = float(item["target_quantity"] or 0)
        if target <= 0:
            target = float(item["min_quantity"])
        amount = max(1, target - float(item["quantity"]))
        amount_text = f"{fmt_num(amount)} {esc(item['unit'])}"
        meta = " · ".join(
            filter(
                None,
                [
                    f"Har {fmt_num(item['quantity'])}",
                    f"Minimum {fmt_num(item['min_quantity'])}",
                    f"Mål {fmt_num(target)}" if target > float(item["min_quantity"]) else "",
                    item["location"],
                ],
            )
        )
        thumb = (
            f'<img class="shopping-thumb" src="{esc(item["image_url"])}" alt="">'
            if item["image_url"]
            else '<div class="shopping-thumb" aria-hidden="true"></div>'
        )
        checkmark = '<path d="m7 12 3 3 7-7"/>' if checked else ""
        next_value = "0" if checked else "1"
        action_label = "Fjern avkrysning" if checked else "Legg i kurven"
        return f"""
          <form class="shopping-row {"checked" if checked else ""}" method="post"
                action="item/{item['id']}/shopping-check"
                data-copy="{esc(amount_text)} {esc(item['name'])}">
            <input type="hidden" name="checked" value="{next_value}">
            <button class="shopping-check" aria-label="{action_label}: {esc(item['name'])}">
              <svg viewBox="0 0 24 24" aria-hidden="true">{checkmark}</svg>
            </button>
            {thumb}
            <div class="shopping-copy">
              <p class="shopping-name">{esc(item['name'])}</p>
              <div class="shopping-amount">Kjøp {amount_text}</div>
              <div class="shopping-meta">{esc(meta)}</div>
            </div>
          </form>
        """

    def grouped_rows(group_items):
        groups = {}
        for item in group_items:
            label = (item["category"] or "").strip() or "Annet"
            groups.setdefault(label, []).append(item)
        return "".join(
            f"""
              <section class="shopping-group">
                <div class="shopping-group-heading">
                  <span>{esc(label)}</span>
                  <span class="shopping-group-count">{len(group)}</span>
                </div>
                <div class="shopping-list">{"".join(shopping_row(item) for item in group)}</div>
              </section>
            """
            for label, group in sorted(groups.items(), key=lambda entry: entry[0].lower())
        )

    remaining_html = grouped_rows(remaining)
    completed_html = "".join(shopping_row(item) for item in completed)
    if not items:
        list_content = """
          <div class="card">
            <strong>Handlelisten er tom</strong>
            <p class="muted">Varer dukker opp her når beholdningen når minimumsgrensen.</p>
          </div>
        """
    else:
        open_content = (
            f'<section class="shopping-groups">{remaining_html}</section>'
            if remaining_html
            else '<div class="card"><strong>Alt er lagt i kurven ✓</strong></div>'
        )
        completed_content = (
            f"""
              <details class="shopping-completed">
                <summary>I kurven ({len(completed)})</summary>
                <section class="shopping-list">{completed_html}</section>
              </details>
            """
            if completed_html
            else ""
        )
        list_content = open_content + completed_content

    share_button = (
        '<button class="btn" id="share-shopping" type="button">Del liste</button>'
        if remaining
        else ""
    )
    return f"""
      <section class="shopping-header">
        <div>
          <h1>Handleliste</h1>
          <p class="muted">{len(remaining)} {"vare" if len(remaining) == 1 else "varer"} igjen</p>
        </div>
        {share_button}
      </section>
      {list_content}
      <script>
        const shareButton = document.getElementById("share-shopping");
        shareButton?.addEventListener("click", async () => {{
          const lines = [...document.querySelectorAll(".shopping-row:not(.checked)")]
            .map((row) => "• " + row.dataset.copy);
          const text = "Handleliste\\n" + lines.join("\\n");
          try {{
            if (navigator.share) {{
              await navigator.share({{ title: "Handleliste", text }});
            }} else {{
              await navigator.clipboard.writeText(text);
              shareButton.textContent = "Kopiert";
            }}
          }} catch (error) {{
            if (error.name !== "AbortError") {{
              shareButton.textContent = "Kunne ikke dele";
            }}
          }}
        }});
      </script>
    """


def organize_page():
    locations = distinct_values("location")
    categories = distinct_values("category")
    alerts = create_alerts_payload()
    alert_summary = alerts["summary"]
    if alert_summary["total"]:
        alert_status = (
            f'{alert_summary["low_stock"]} må kjøpes · '
            f'{alert_summary["best_before"]} med nær best før'
        )
    else:
        alert_status = "Ingen varer krever oppmerksomhet nå"
    nfc = get_home_assistant_nfc_state()
    nfc_ready = nfc["status"] == "connected"
    nfc_label = "Tilkoblet" if nfc_ready else "Kobler til"
    nfc_dot = "" if nfc_ready else " waiting"
    with db() as conn:
        location_rows = conn.execute(
            """
            select l.name,
                   count(i.id) as item_count,
                   lt.tag_id
            from locations l
            left join items i on i.location = l.name
            left join location_tags lt on lt.location = l.name
            group by l.id, l.name, lt.tag_id
            order by lower(l.name)
            """
        ).fetchall()
    location_entries = []
    for row in location_rows:
        location = row["name"]
        encoded_location = quote(location, safe="")
        filtered_url = ".?" + urlencode({"location": location, "kind": "all"})
        tag_label = "Bytt NFC-tag" if row["tag_id"] else "Koble NFC-tag"
        direct_action = (
            f'<a class="btn" href="location/{encoded_location}/tag-open-setup">Direkte åpning</a>'
            if row["tag_id"]
            else ""
        )
        location_entries.append(
            f"""
              <li class="location-entry">
                <div>
                  <strong>{esc(location)}</strong>
                  <span class="muted">{row['item_count']} vare{'r' if row['item_count'] != 1 else ''}</span>
                </div>
                <div class="actions">
                  <a class="btn" href="{esc(filtered_url)}">Vis varer</a>
                  <form method="post" action="location/{encoded_location}/tag-link/start">
                    <button class="btn primary">{tag_label}</button>
                  </form>
                  {direct_action}
                </div>
              </li>
            """
        )
    location_list = "".join(location_entries) or "<li>Ingen steder ennå</li>"
    category_list = "".join(f"<li>{esc(value)}</li>" for value in categories) or "<li>Ingen kategorier ennå</li>"
    return f"""
    <h1>Steder og kategorier</h1>
    <section class="card">
      <h2>Systemstatus</h2>
      <p class="muted">Det viktigste samlet på ett sted.</p>
      <div class="status-grid">
        <div class="status-item">
          <strong><span class="status-dot{nfc_dot}"></span>NFC: {nfc_label}</strong>
          <small class="muted">{esc(nfc["message"])}</small>
        </div>
        <div class="status-item">
          <strong><span class="status-dot"></span>Produktoppslag: Klar</strong>
          <small class="muted">Brukes automatisk etter strekkodeskanning.</small>
        </div>
        <div class="status-item">
          <strong><span class="status-dot"></span>Backup: Klar</strong>
          <small class="muted">Komplett kopi kan lastes ned når som helst.</small>
        </div>
      </div>
      <div class="actions" style="margin-top: 10px;">
        <a class="btn" href="activity">Vis historikk</a>
        <a class="btn" href="export/items.csv">Eksporter regneark</a>
      </div>
    </section>
    <section class="grid">
      <div class="card">
        <h2>Plasseringer</h2>
        <form class="stack" method="post" action="organize">
          <input type="hidden" name="kind" value="location">
          <label>Nytt sted
            <input name="name" placeholder="Kjøkken > Kjøleskap" required>
          </label>
          <button class="btn primary">Legg til sted</button>
        </form>
        <ul class="location-list">{location_list}</ul>
      </div>
      <div class="card">
        <h2>Kategorier</h2>
        <form class="stack" method="post" action="organize">
          <input type="hidden" name="kind" value="category">
          <label>Ny kategori
            <input name="name" placeholder="Matvarer, kabler, verktøy" required>
          </label>
          <button class="btn primary">Legg til kategori</button>
        </form>
        <ul>{category_list}</ul>
      </div>
    </section>
    <section class="card" style="margin-top: 10px;">
      <h2>Home Assistant-varsler</h2>
      <p class="muted">{esc(alert_status)}</p>
      <p>Hjemmelager kan nå levere én samlet status for handleliste og best før til en Home Assistant-automatisering.</p>
      <div class="actions">
        <a class="btn primary" href="api/alerts">Test varseldata</a>
      </div>
      <p class="field-help">Ferdig sensor og automatisering følger med under <strong>examples</strong>. Velg selv hvilken mobil som skal motta varselet.</p>
    </section>
    <section class="card" style="margin-top: 10px;">
      <h2>Data og sikkerhetskopi</h2>
      <p class="muted">Last ned en komplett kopi av varer, bilder, steder, kategorier og historikk.</p>
      <a class="btn primary" href="backup/download">Last ned sikkerhetskopi</a>
      <a class="btn" href="export/items.csv">Eksporter lesbar CSV</a>
      <p class="field-help">Filen endrer ingenting i lageret. Oppbevar den et trygt sted.</p>
      <details class="form-section" style="margin-top: 10px;">
        <summary>
          <span class="form-section-summary">
            Gjenopprett fra fil
            <small>Kontrolleres før data erstattes</small>
          </span>
        </summary>
        <div class="form-section-content">
          <form class="stack" method="post" action="backup/restore"
                enctype="multipart/form-data"
                onsubmit="return confirm('Vil du erstatte dagens lager med innholdet i sikkerhetskopien?')">
            <label style="padding-top: 10px;">Sikkerhetskopifil
              <input name="backup_file" type="file" accept=".json,application/json" required>
            </label>
            <label>
              <span><input name="confirm_restore" type="checkbox" value="1" required>
                Jeg forstår at dagens lager blir erstattet</span>
            </label>
            <button class="btn warn">Gjenopprett lager</button>
          </form>
        </div>
      </details>
    </section>
    """


def activity_page():
    events = recent_events()
    if events:
        rows = "".join(
            f"""
            <li class="history-row">
              <span>{
                  f'<a href="item/{event["item_id"]}">{esc(event_description(event))}</a>'
                  if event.get("item_name") and event.get("item_id")
                  else esc(event_description(event))
              }</span>
              <time datetime="{datetime.fromtimestamp(int(event['created_at'])).isoformat()}">
                {format_event_time(event["created_at"])}
              </time>
            </li>
            """
            for event in events
        )
        content = f'<ol class="history-list">{rows}</ol>'
    else:
        content = """
        <div class="empty-state">
          <h2>Ingen historikk ennå</h2>
          <p class="muted">Endringer dukker opp her når du begynner å bruke lageret.</p>
          <a class="btn primary" href="new">Legg til første vare</a>
        </div>
        """
    return f"""
    <div class="page-heading">
      <div>
        <h1>Historikk</h1>
        <p class="muted">De siste endringene i lageret.</p>
      </div>
      <a class="btn" href="organize">Tilbake</a>
    </div>
    <section class="card">{content}</section>
    """


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print(f"{self.address_string()} - {fmt % args}", flush=True)

    def ingress_base(self):
        return self.headers.get("X-Ingress-Path", "").rstrip("/")

    def route_path(self):
        path = unquote(urlparse(self.path).path)
        base = self.ingress_base()
        if base and path.startswith(base):
            path = path[len(base):] or "/"
        return path.strip("/")

    def read_body(self):
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length) if length else b""
        content_type = self.headers.get("Content-Type", "")
        if "application/json" in content_type:
            return json.loads(raw.decode("utf-8") or "{}")
        if "multipart/form-data" in content_type:
            return parse_multipart_form(raw, content_type)
        parsed = parse_qs(raw.decode("utf-8"))
        return {key: values[-1] for key, values in parsed.items()}

    def send_html(self, title, body, status=HTTPStatus.OK):
        data = page(title, body, self.ingress_base()).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, payload, status=HTTPStatus.OK):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_download(self, data, filename, content_type):
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_static(self, rel_path, content_type):
        target = (STATIC_DIR / rel_path).resolve()
        if not str(target).startswith(str(STATIC_DIR.resolve())) or not target.is_file():
            self.send_html("Ikke funnet", "<h1>Ikke funnet</h1>", HTTPStatus.NOT_FOUND)
            return
        data = target.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "public, max-age=31536000")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def redirect(self, target):
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", self.ingress_base() + "/" + target.lstrip("/"))
        self.end_headers()

    def do_GET(self):
        path = self.route_path()
        if path == "static/zxing-browser.min.js":
            self.send_static("zxing-browser.min.js", "text/javascript; charset=utf-8")
            return

        if path == "backup/download":
            payload = create_backup_payload()
            data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
            filename = f"hjemmelager-backup-{date.today().isoformat()}.json"
            self.send_download(data, filename, "application/json; charset=utf-8")
            return

        if path == "export/items.csv":
            filename = f"hjemmelager-{date.today().isoformat()}.csv"
            self.send_download(
                inventory_csv_bytes(),
                filename,
                "text/csv; charset=utf-8",
            )
            return

        if path in ("", "items"):
            query = parse_qs(urlparse(self.path).query)
            search = (query.get("q") or [""])[0].strip()
            category = (query.get("category") or [""])[0].strip()
            location = (query.get("location") or [""])[0].strip()
            view = (query.get("view") or ["cards"])[0]
            kind_view = (query.get("kind") or ["consumable"])[0]
            if kind_view not in ("consumable", "thing", "all"):
                kind_view = "consumable"
            low_only = (query.get("low") or [""])[0] == "1"
            expiry_only = (query.get("expiry") or [""])[0] == "1"
            if kind_view == "thing":
                low_only = False
                expiry_only = False
            where, params = build_item_filters(
                "",
                category,
                location,
                low_only,
                "" if kind_view == "all" else kind_view,
                expiry_only,
            )
            items = list_items(
                where,
                params,
                sort="best_before" if expiry_only else "default",
            )
            if search:
                items = [item for item in items if item_matches_search(item, search)]
            categories = distinct_values("category")
            locations = distinct_values("location")
            consumable_count = count_items("consumable")
            thing_count = count_items("thing")
            expiry_threshold = (date.today() + timedelta(days=14)).isoformat()
            expiring_count = len(
                list_items(
                    "kind = 'consumable' and best_before != '' and best_before <= ?",
                    (expiry_threshold,),
                )
            )
            summary = dashboard_summary()
            recent_summary = (
                esc(event_description(summary["recent"]))
                if summary["recent"]
                else "Ingen endringer ennå"
            )
            deleted_id = (query.get("deleted") or [""])[0]
            deleted_notice = (
                deletion_notice(int(deleted_id))
                if deleted_id.isdigit()
                else ""
            )
            current_params = {
                "q": search,
                "category": category,
                "location": location,
                "low": "1" if low_only else "",
                "expiry": "1" if expiry_only else "",
                "view": view,
                "kind": kind_view,
            }
            card_url = query_link(current_params, view="cards")
            list_url = query_link(current_params, view="list")
            low_url = query_link(
                current_params,
                low="" if low_only else "1",
                expiry="",
            )
            expiry_url = query_link(
                {
                    "view": view,
                    "kind": "consumable" if kind_view == "thing" else kind_view,
                },
                expiry="" if expiry_only else "1",
            )
            clear_url = query_link({"view": view, "kind": kind_view})
            consumable_url = query_link({"view": view, "kind": "consumable"})
            thing_url = query_link({"view": view, "kind": "thing"})
            all_url = query_link({"view": view, "kind": "all"})
            filtered = bool(
                search
                or category
                or location
                or low_only
                or expiry_only
            )
            empty_html = inventory_empty_state(
                kind_view,
                filtered=filtered,
                clear_url=clear_url,
            )
            if view == "list":
                items_html = "".join(item_row(item) for item in items) or empty_html
                items_html = f'<section class="item-list">{items_html}</section>'
            else:
                items_html = "".join(item_card(item) for item in items) or empty_html
                items_html = f'<section class="grid">{items_html}</section>'
            low_filter = (
                f'<a class="btn {"active" if low_only else ""}" href="{low_url}">Må kjøpes</a>'
                if kind_view != "thing"
                else ""
            )
            expiry_notice = ""
            if (
                expiring_count
                and kind_view != "thing"
                and (not filtered or expiry_only)
            ):
                expiry_label = (
                    f"{expiring_count} vare{'r' if expiring_count != 1 else ''} "
                    "er utløpt eller bør brukes snart"
                )
                expiry_action = "Vis alle" if expiry_only else "Vis"
                expiry_notice = f"""
                  <a class="expiry-notice" href="{expiry_url}">
                    <span class="expiry-notice-copy">
                      <svg viewBox="0 0 24 24" aria-hidden="true">
                        <circle cx="12" cy="12" r="9"></circle>
                        <path d="M12 7v5l3 2"></path>
                      </svg>
                      <span>{expiry_label}</span>
                    </span>
                    <span class="expiry-notice-action">{expiry_action} →</span>
                  </a>
                """
            body = f"""
              {deleted_notice}
              <h1 class="inventory-title">Mitt lager</h1>
              <section class="dashboard-strip" aria-label="Kort status">
                <a class="dashboard-stat" href="{all_url}">
                  <strong>{summary["total"]}</strong><span>i lageret</span>
                </a>
                <a class="dashboard-stat {"attention" if summary["low_stock"] else ""}" href="low-stock">
                  <strong>{summary["low_stock"]}</strong><span>må kjøpes</span>
                </a>
                <a class="dashboard-stat {"attention" if summary["best_before"] else ""}" href="{expiry_url}">
                  <strong>{summary["best_before"]}</strong><span>best før</span>
                </a>
              </section>
              <div class="dashboard-recent">
                <span>Sist: {recent_summary}</span>
                <a href="activity">Historikk</a>
              </div>
              <nav class="inventory-tabs" aria-label="Type lager">
                <a class="inventory-tab {"active" if kind_view == "consumable" else ""}" href="{consumable_url}">
                  <span>Forbruk</span><span class="inventory-tab-count">{consumable_count}</span>
                </a>
                <a class="inventory-tab {"active" if kind_view == "thing" else ""}" href="{thing_url}">
                  <span>Ting</span><span class="inventory-tab-count">{thing_count}</span>
                </a>
                <a class="inventory-tab {"active" if kind_view == "all" else ""}" href="{all_url}">
                  <span>Alle</span><span class="inventory-tab-count">{consumable_count + thing_count}</span>
                </a>
              </nav>
              <form method="get" action="." class="toolbar">
                <input type="hidden" name="view" value="{esc(view)}">
                <input type="hidden" name="kind" value="{esc(kind_view)}">
                <div class="search-row">
                  <label>Søk
                    <input name="q" value="{esc(search)}" placeholder="Søk etter vare, sted eller kode">
                  </label>
                  <button class="btn primary">Søk</button>
                </div>
                <details class="filter-panel" open>
                  <summary title="Filtre">
                    <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 5h16l-6.5 7.2V18l-3 1.5v-7.3z"/></svg>
                    <span class="sr-only">Filtre</span>
                  </summary>
                  <div class="filters">
                    <label>Plassering
                      <select name="location">{option_list(locations, location, "Alle steder")}</select>
                    </label>
                    <label>Kategori
                      <select name="category">{option_list(categories, category, "Alle kategorier")}</select>
                    </label>
                    <label class="expiry-filter-label">
                      <input type="checkbox" name="expiry" value="1" {"checked" if expiry_only else ""}>
                      Best før innen 14 dager
                    </label>
                    <button class="btn primary">Bruk filtre</button>
                    <a class="btn" href="{clear_url}">Nullstill</a>
                  </div>
                </details>
                <div class="view-switch">
                  <a class="btn {"active" if view != "list" else ""}" href="{card_url}">Kort</a>
                  <a class="btn {"active" if view == "list" else ""}" href="{list_url}">Liste</a>
                  {low_filter}
                </div>
              </form>
              {expiry_notice}
              {items_html}
              <script>
                if (window.matchMedia("(max-width: 680px)").matches) {{
                  document.querySelector(".filter-panel")?.removeAttribute("open");
                }}
              </script>
            """
            self.send_html("Varer", body)
            return

        if path == "new":
            query = parse_qs(urlparse(self.path).query)
            if not query:
                self.send_html("Legg til", new_item_start_page())
                return
            tag_id = (query.get("tag_id") or [""])[0]
            barcode = (query.get("barcode") or [""])[0]
            kind = (query.get("kind") or ["consumable"])[0]
            kind = kind if kind in ("consumable", "thing") else "consumable"
            title = "Ny gjenstand" if kind == "thing" else "Ny vare"
            self.send_html(
                title,
                f"<h1>{title}</h1>{item_form(tag_id=tag_id, barcode=barcode, kind=kind)}",
            )
            return

        if path == "scan":
            self.send_html("Scan kode", scan_page())
            return

        if path == "scan/result":
            code = (parse_qs(urlparse(self.path).query).get("code") or [""])[0]
            self.redirect(scanned_code_redirect(code))
            return

        if path == "tag/open":
            tag_id = (parse_qs(urlparse(self.path).query).get("tag_id") or [""])[0]
            result = touch_tag(tag_id)
            if result.get("item_id"):
                self.redirect(f"item/{result['item_id']}?scanned=1")
                return
            if result.get("location"):
                self.redirect(".?" + urlencode({"location": result["location"], "kind": "all"}))
                return
            self.send_html(
                "Ukjent NFC-tag",
                """
                  <section class="card stack">
                    <h1>Taggen er ikke koblet</h1>
                    <p>Koble taggen til en vare eller plassering i Hjemmelager og prøv igjen.</p>
                    <a class="btn primary" href=".">Åpne lageret</a>
                  </section>
                """,
                HTTPStatus.NOT_FOUND,
            )
            return

        if path == "organize":
            self.send_html("Steder og kategorier", organize_page())
            return

        if path == "activity":
            self.send_html("Historikk", activity_page())
            return

        if path == "low-stock":
            self.send_html("Lav beholdning", shopping_list_page())
            return

        if path.startswith("location/"):
            parts = path.split("/")
            location = parts[1] if len(parts) > 1 else ""
            if location not in distinct_values("location"):
                self.send_html("Ikke funnet", "<h1>Plasseringen finnes ikke</h1>", HTTPStatus.NOT_FOUND)
                return
            if len(parts) == 3 and parts[2] == "tag-link":
                session = get_location_tag_link_session(location)
                self.send_html(
                    "Koble NFC-tag til plassering",
                    location_tag_link_page(location, session),
                )
                return
            if len(parts) == 3 and parts[2] == "tag-open-setup":
                self.send_html(
                    "Direkte NFC-åpning",
                    location_tag_open_setup_page(location),
                )
                return

        if path.startswith("item/"):
            parts = path.split("/")
            if len(parts) == 2 and parts[1].isdigit():
                item = get_item(int(parts[1]))
                if not item:
                    self.send_html("Ikke funnet", "<h1>Ikke funnet</h1>", HTTPStatus.NOT_FOUND)
                    return
                img = f'<img class="item-hero" src="{esc(item["image_url"])}" alt="{esc(item["name"])}">' if item["image_url"] else ""
                badges = item_badges(item, "Lav beholdning")
                price_text = fmt_price(item["price"]) or "Ikke satt"
                best_before_text = item["best_before"] or "Ikke satt"
                is_consumable = item["kind"] == "consumable"
                quantity_text = (
                    f"{fmt_num(item['quantity'])} {esc(item['unit'])} på lager"
                    if is_consumable
                    else f"{fmt_num(item['quantity'])} {esc(item['unit'])}"
                )
                opened_text = (
                    f'<p class="muted">{fmt_num(item["opened_quantity"])} {esc(item["unit"])} åpne</p>'
                    if is_consumable
                    else ""
                )
                stock_details = (
                    f"""
                      <p class="muted">Pris: {esc(price_text)} · Tidligste best før: {esc(best_before_text)}</p>
                      <p class="muted">Varsle ved: {fmt_num(item["min_quantity"])} · Fyll opp til: {
                          fmt_num(item["target_quantity"])
                          if float(item["target_quantity"] or 0) > 0
                          else fmt_num(item["min_quantity"])
                      }</p>
                    """
                    if is_consumable
                    else ""
                )
                identifiers = "".join(
                    filter(
                        None,
                        [
                            f'<p class="muted">NFC: {esc(item["tag_id"])}</p>' if item["tag_id"] else "",
                            f'<p class="muted">Kode: {esc(item["barcode"])}</p>' if item["barcode"] else "",
                        ],
                    )
                )
                consumable_actions = (
                    f"""
                      <form method="post" action="item/{item['id']}/open"><button class="btn">Åpne 1 pakke</button></form>
                      <form method="post" action="item/{item['id']}/adjust-opened"><input type="hidden" name="delta" value="-1"><button class="btn">Bruk 1 åpen</button></form>
                    """
                    if is_consumable
                    else ""
                )
                expiry_panel = expiry_batches_panel(item)
                tag_action_label = "Bytt NFC-tag" if item["tag_id"] else "Koble NFC-tag"
                shopping_toggle = (
                    f"""
                      <form method="post" action="item/{item['id']}/shopping-toggle">
                        <input type="hidden" name="enabled" value="{
                            "0" if item["shopping_enabled"] else "1"
                        }">
                        <button class="btn">{
                            "Ikke på handleliste"
                            if item["shopping_enabled"]
                            else "Bruk handleliste"
                        }</button>
                      </form>
                    """
                    if is_consumable
                    else ""
                )
                query = parse_qs(urlparse(self.path).query)
                created_notice = (
                    created_item_notice(item)
                    if (query.get("created") or ["0"])[0] == "1"
                    else ""
                )
                changed_notice = (
                    adjustment_notice(item)
                    if (query.get("changed") or ["0"])[0] == "1"
                    else ""
                )
                scanned_notice = (
                    """
                      <section class="created-notice">
                        <span class="created-check" aria-hidden="true">✓</span>
                        <h2>Åpnet fra NFC-tag</h2>
                      </section>
                    """
                    if (query.get("scanned") or ["0"])[0] == "1"
                    else ""
                )
                direct_open_action = (
                    f'<a class="btn" href="item/{item["id"]}/tag-open-setup">'
                    "Direkte NFC-åpning</a>"
                    if item["tag_id"]
                    else ""
                )
                body = f"""
                  {created_notice}
                  {changed_notice}
                  {scanned_notice}
                  <div class="card item-detail-card">
                    {img}
                    <div class="item-title"><h1>{esc(item['name'])}</h1>{badges}</div>
                    <div class="qty">{quantity_text}</div>
                    {opened_text}
                    <p class="muted">{esc(item['category'])} {("· " + esc(item['location'])) if item['location'] else ""}</p>
                    {stock_details}
                    {identifiers}
                    {f"<p>{esc(item['note'])}</p>" if item['note'] else ""}
                    <div class="actions">
                      <form method="post" action="item/{item['id']}/adjust"><input type="hidden" name="delta" value="-1"><button class="btn">Fjern 1</button></form>
                      <form method="post" action="item/{item['id']}/adjust"><input type="hidden" name="delta" value="1"><button class="btn primary">Legg til 1</button></form>
                      <form method="post" action="item/{item['id']}/adjust"><input type="hidden" name="delta" value="5"><button class="btn">Legg til 5</button></form>
                      <form method="post" action="item/{item['id']}/adjust"><input type="hidden" name="delta" value="10"><button class="btn">Legg til 10</button></form>
                      <form class="quantity-custom" method="post" action="item/{item['id']}/adjust">
                        <label>Eget antall
                          <input name="delta" type="number" step="0.01" inputmode="decimal" required placeholder="15 eller -3">
                        </label>
                        <button class="btn">Endre</button>
                      </form>
                      {consumable_actions}
                      <form method="post" action="item/{item['id']}/tag-link/start">
                        <button class="btn">{tag_action_label}</button>
                      </form>
                      {direct_open_action}
                      {shopping_toggle}
                      <a class="btn" href="item/{item['id']}/edit">Rediger</a>
                    </div>
                    {expiry_panel}
                  </div>
                  <details class="card danger-zone">
                    <summary>Flere valg</summary>
                    <div class="danger-zone-content">
                      <p class="muted">Sletting fjerner varen, NFC-koblingen og historikken permanent.</p>
                          <form method="post" action="item/{item['id']}/delete"
                            onsubmit="return confirm('Vil du slette varen? Du får mulighet til å angre etterpå.')">
                        <button class="btn danger">Slett vare</button>
                      </form>
                    </div>
                  </details>
                """
                self.send_html(item["name"], body)
                return
            if (
                len(parts) == 3
                and parts[2] == "tag-link"
                and parts[1].isdigit()
            ):
                item = get_item(int(parts[1]))
                if not item:
                    self.send_html("Ikke funnet", "<h1>Ikke funnet</h1>", HTTPStatus.NOT_FOUND)
                    return
                self.send_html(
                    "Koble NFC-tag",
                    tag_link_page(item, get_tag_link_session(item["id"])),
                )
                return
            if (
                len(parts) == 3
                and parts[2] == "tag-open-setup"
                and parts[1].isdigit()
            ):
                item = get_item(int(parts[1]))
                if not item:
                    self.send_html("Ikke funnet", "<h1>Ikke funnet</h1>", HTTPStatus.NOT_FOUND)
                    return
                self.send_html(
                    "Direkte NFC-åpning",
                    tag_open_setup_page(item),
                )
                return
            if len(parts) == 3 and parts[2] == "edit" and parts[1].isdigit():
                item = get_item(int(parts[1]))
                if not item:
                    self.send_html("Ikke funnet", "<h1>Ikke funnet</h1>", HTTPStatus.NOT_FOUND)
                    return
                self.send_html("Rediger", f"<h1>Rediger</h1>{item_form(item)}")
                return

        if path == "api/items":
            self.send_json({"items": list_items()})
            return

        if path == "api/low-stock":
            self.send_json({"items": list_items("kind = 'consumable' and shopping_enabled = 1 and min_quantity > 0 and quantity <= min_quantity")})
            return

        if path == "api/alerts":
            query = parse_qs(urlparse(self.path).query)
            days = (query.get("days") or ["14"])[0]
            self.send_json(create_alerts_payload(days))
            return

        if path == "api/locations":
            self.send_json({"locations": distinct_values("location")})
            return

        if path == "api/categories":
            self.send_json({"categories": distinct_values("category")})
            return

        if path == "api/tag-link/status":
            query = parse_qs(urlparse(self.path).query)
            item_id = int((query.get("item_id") or ["0"])[0] or 0)
            session = get_tag_link_session(item_id)
            if not session:
                self.send_json(
                    {
                        "status": "cancelled",
                        "message": "Ingen aktiv tag-kobling.",
                        "seconds_left": 0,
                        "home_assistant": get_home_assistant_nfc_state(),
                    }
                )
                return
            session["home_assistant"] = get_home_assistant_nfc_state()
            self.send_json(session)
            return

        if path == "api/location-tag-link/status":
            query = parse_qs(urlparse(self.path).query)
            location = (query.get("location") or [""])[0]
            session = get_location_tag_link_session(location)
            if not session:
                self.send_json(
                    {
                        "status": "cancelled",
                        "message": "Ingen aktiv tag-kobling.",
                        "seconds_left": 0,
                        "home_assistant": get_home_assistant_nfc_state(),
                    }
                )
                return
            session["home_assistant"] = get_home_assistant_nfc_state()
            self.send_json(session)
            return

        if path == "api/product-lookup":
            query = parse_qs(urlparse(self.path).query)
            barcode = (query.get("barcode") or [""])[0]
            self.send_json(lookup_product(barcode))
            return

        if path == "api/version":
            self.send_json(
                {
                    "name": APP_NAME,
                    "version": APP_VERSION,
                    "codename": APP_CODENAME,
                }
            )
            return

        self.send_html("Ikke funnet", "<h1>Ikke funnet</h1>", HTTPStatus.NOT_FOUND)

    def do_POST(self):
        path = self.route_path()
        try:
            data = self.read_body()
        except Exception as exc:
            if path == "backup/restore":
                self.send_html(
                    "Kunne ikke gjenopprette",
                    f"""
                      <div class="card">
                        <h1>Kunne ikke gjenopprette</h1>
                        <p>{esc(exc)}</p>
                        <a class="btn" href="organize">Tilbake til Mer</a>
                      </div>
                    """,
                    HTTPStatus.BAD_REQUEST,
                )
                return
            if path == "new" or (
                path.startswith("item/") and path.endswith("/edit")
            ):
                self.send_html(
                    "Kunne ikke lagre bildet",
                    f"""
                      <div class="card">
                        <h1>Bildet kunne ikke lagres</h1>
                        <p>{esc(exc)}</p>
                        <button class="btn primary" onclick="history.back()">Gå tilbake</button>
                      </div>
                    """,
                    HTTPStatus.BAD_REQUEST,
                )
                return
            self.send_json({"error": f"Invalid body: {exc}"}, HTTPStatus.BAD_REQUEST)
            return

        if path == "backup/restore":
            if str(data.get("confirm_restore", "0")).lower() not in (
                "1",
                "true",
                "on",
                "yes",
            ):
                self.send_html(
                    "Bekreft gjenoppretting",
                    """
                      <div class="card">
                        <h1>Bekreft gjenoppretting</h1>
                        <p>Du må bekrefte at dagens lager blir erstattet.</p>
                        <a class="btn" href="organize">Tilbake til Mer</a>
                      </div>
                    """,
                    HTTPStatus.BAD_REQUEST,
                )
                return
            try:
                payload = parse_backup_bytes(data.get("backup_file_bytes"))
                result = restore_backup_payload(payload)
            except (ValueError, sqlite3.Error, OSError) as exc:
                self.send_html(
                    "Kunne ikke gjenopprette",
                    f"""
                      <div class="card">
                        <h1>Ingen data ble erstattet</h1>
                        <p>{esc(exc)}</p>
                        <a class="btn" href="organize">Tilbake til Mer</a>
                      </div>
                    """,
                    HTTPStatus.BAD_REQUEST,
                )
                return
            self.send_html(
                "Gjenoppretting fullført",
                f"""
                  <div class="card">
                    <h1>Gjenoppretting fullført ✓</h1>
                    <p>{result["items"]} varer og {result["events"]} historikkhendelser ble lest inn.</p>
                    <p class="muted">En automatisk før-kopi er lagret som
                      <strong>{esc(result["before_filename"])}</strong> i add-onens dataområde.</p>
                    <a class="btn primary" href=".">Åpne lageret</a>
                  </div>
                """,
            )
            return

        if path == "new":
            try:
                item = create_item(data)
            except ValueError as exc:
                self.send_html(
                    "Kunne ikke lagre bildet",
                    f"""
                      <div class="card">
                        <h1>Bildet kunne ikke lagres</h1>
                        <p>{esc(exc)}</p>
                        <button class="btn primary" onclick="history.back()">Gå tilbake</button>
                      </div>
                    """,
                    HTTPStatus.BAD_REQUEST,
                )
                return
            except sqlite3.IntegrityError:
                self.send_html("Tag finnes", "<h1>Tag-id er allerede i bruk</h1>", HTTPStatus.CONFLICT)
                return
            self.redirect(new_item_redirect(item, data))
            return

        if path == "organize":
            create_registry_entry(data.get("kind"), data.get("name"))
            self.redirect("organize")
            return

        if path == "api/items":
            try:
                item = create_item(data)
            except sqlite3.IntegrityError:
                self.send_json({"error": "tag_id already exists"}, HTTPStatus.CONFLICT)
                return
            self.send_json({"item": item}, HTTPStatus.CREATED)
            return

        if path.startswith("location/"):
            parts = path.split("/")
            location = parts[1] if len(parts) > 1 else ""
            if (
                len(parts) == 4
                and parts[2] == "tag-link"
                and parts[3] in ("start", "cancel")
            ):
                if parts[3] == "start":
                    session = start_location_tag_link(location)
                    if not session:
                        self.send_html(
                            "Ikke funnet",
                            "<h1>Plasseringen finnes ikke</h1>",
                            HTTPStatus.NOT_FOUND,
                        )
                        return
                    self.redirect(f"location/{quote(location, safe='')}/tag-link")
                    return
                cancel_location_tag_link(location)
                self.redirect("organize")
                return

        if path.startswith("item/"):
            parts = path.split("/")
            if (
                len(parts) == 3
                and parts[2] == "shopping-toggle"
                and parts[1].isdigit()
            ):
                item = set_shopping_enabled(
                    int(parts[1]),
                    str(data.get("enabled", "0")).lower() in ("1", "true", "on", "yes"),
                )
                if not item:
                    self.send_html("Ikke funnet", "<h1>Ikke funnet</h1>", HTTPStatus.NOT_FOUND)
                    return
                self.redirect(f"item/{parts[1]}")
                return
            if len(parts) == 3 and parts[2] == "delete" and parts[1].isdigit():
                deletion_id = delete_item(int(parts[1]))
                if not deletion_id:
                    self.send_html("Ikke funnet", "<h1>Ikke funnet</h1>", HTTPStatus.NOT_FOUND)
                    return
                self.redirect(f".?deleted={deletion_id}")
                return
            if (
                len(parts) == 4
                and parts[2] == "tag-link"
                and parts[3] == "start"
                and parts[1].isdigit()
            ):
                session = start_tag_link(int(parts[1]))
                if not session:
                    self.send_html("Ikke funnet", "<h1>Ikke funnet</h1>", HTTPStatus.NOT_FOUND)
                    return
                self.redirect(f"item/{parts[1]}/tag-link")
                return
            if (
                len(parts) == 4
                and parts[2] == "tag-link"
                and parts[3] == "cancel"
                and parts[1].isdigit()
            ):
                cancel_tag_link(int(parts[1]))
                self.redirect(f"item/{parts[1]}")
                return
            if (
                len(parts) == 4
                and parts[2] == "expiry"
                and parts[1].isdigit()
                and parts[3] in ("add", "clear")
            ):
                try:
                    if parts[3] == "add":
                        item = add_expiry_batch(
                            int(parts[1]),
                            data.get("quantity"),
                            data.get("best_before"),
                            from_existing=data.get("source") == "existing",
                        )
                    else:
                        item = clear_expiry_batch_date(
                            int(parts[1]), data.get("best_before")
                        )
                except ValueError as exc:
                    self.send_html(
                        "Kunne ikke endre holdbarhet",
                        f"""
                          <section class="card stack">
                            <h1>Kunne ikke endre holdbarhet</h1>
                            <p>{esc(exc)}</p>
                            <a class="btn" href="item/{parts[1]}">Tilbake til varen</a>
                          </section>
                        """,
                        HTTPStatus.BAD_REQUEST,
                    )
                    return
                if not item:
                    self.send_html("Ikke funnet", "<h1>Ikke funnet</h1>", HTTPStatus.NOT_FOUND)
                    return
                self.redirect(f"item/{parts[1]}")
                return
            if len(parts) == 3 and parts[2] == "adjust" and parts[1].isdigit():
                adjust_item(int(parts[1]), parse_float(data.get("delta")), "web")
                self.redirect(f"item/{parts[1]}?changed=1")
                return
            if (
                len(parts) == 3
                and parts[2] == "undo-adjustment"
                and parts[1].isdigit()
            ):
                undo_last_adjustment(int(parts[1]))
                self.redirect(f"item/{parts[1]}")
                return
            if len(parts) == 3 and parts[2] == "open" and parts[1].isdigit():
                open_package(int(parts[1]), "web")
                self.redirect(f"item/{parts[1]}")
                return
            if len(parts) == 3 and parts[2] == "adjust-opened" and parts[1].isdigit():
                adjust_opened_item(int(parts[1]), parse_float(data.get("delta")), "web")
                self.redirect(f"item/{parts[1]}")
                return
            if len(parts) == 3 and parts[2] == "shopping-check" and parts[1].isdigit():
                set_shopping_checked(
                    int(parts[1]),
                    str(data.get("checked", "0")).lower() in ("1", "true", "on", "yes"),
                )
                self.redirect("low-stock")
                return
            if len(parts) == 3 and parts[2] == "edit" and parts[1].isdigit():
                try:
                    item = update_item(int(parts[1]), data)
                except ValueError as exc:
                    self.send_html(
                        "Kunne ikke lagre bildet",
                        f"""
                          <div class="card">
                            <h1>Bildet kunne ikke lagres</h1>
                            <p>{esc(exc)}</p>
                            <button class="btn primary" onclick="history.back()">Gå tilbake</button>
                          </div>
                        """,
                        HTTPStatus.BAD_REQUEST,
                    )
                    return
                except sqlite3.IntegrityError:
                    self.send_html("Tag finnes", "<h1>Tag-id er allerede i bruk</h1>", HTTPStatus.CONFLICT)
                    return
                if not item:
                    self.send_html("Ikke funnet", "<h1>Ikke funnet</h1>", HTTPStatus.NOT_FOUND)
                    return
                self.redirect(f"item/{item['id']}")
                return

        if (
            path.startswith("deleted/")
            and len(path.split("/")) == 3
            and path.split("/")[1].isdigit()
            and path.split("/")[2] == "restore"
        ):
            result = restore_deleted_item(int(path.split("/")[1]))
            if result["status"] == "restored":
                self.redirect(f"item/{result['item']['id']}")
                return
            if result["status"] == "not_found":
                self.send_html(
                    "Ikke funnet",
                    "<h1>Kan ikke angre</h1><p>Den slettede varen finnes ikke lenger.</p>",
                    HTTPStatus.NOT_FOUND,
                )
                return
            self.send_html(
                "Kan ikke angre",
                f"<h1>Kan ikke angre sletting</h1><p>{esc(result.get('message'))}</p>",
                HTTPStatus.CONFLICT,
            )
            return

        if path.startswith("api/items/"):
            parts = path.split("/")
            if len(parts) == 4 and parts[3] == "adjust" and parts[2].isdigit():
                item = adjust_item(int(parts[2]), parse_float(data.get("delta")), data.get("note") or "api")
                if not item:
                    self.send_json({"error": "item not found"}, HTTPStatus.NOT_FOUND)
                    return
                self.send_json({"item": item})
                return
            if len(parts) == 4 and parts[3] == "open" and parts[2].isdigit():
                item = open_package(int(parts[2]), data.get("note") or "api")
                if not item:
                    self.send_json({"error": "item not found"}, HTTPStatus.NOT_FOUND)
                    return
                self.send_json({"item": item})
                return
            if len(parts) == 4 and parts[3] == "adjust-opened" and parts[2].isdigit():
                item = adjust_opened_item(int(parts[2]), parse_float(data.get("delta")), data.get("note") or "api")
                if not item:
                    self.send_json({"error": "item not found"}, HTTPStatus.NOT_FOUND)
                    return
                self.send_json({"item": item})
                return

        if path.startswith("api/tag/"):
            parts = path.split("/")
            if len(parts) >= 4:
                tag_id = parts[2]
                action = parts[3]
                if action == "touch":
                    result = touch_tag(tag_id)
                    if result["status"] == "not_found":
                        self.send_json({"error": "tag not found", "tag_id": tag_id, "create_path": f"new?tag_id={tag_id}"}, HTTPStatus.NOT_FOUND)
                        return
                    if result["status"] == "conflict":
                        self.send_json(result, HTTPStatus.CONFLICT)
                        return
                    self.send_json(result)
                    return
                if action == "adjust":
                    item = get_item_by_tag(tag_id)
                    if not item:
                        self.send_json({"error": "tag not found", "tag_id": tag_id}, HTTPStatus.NOT_FOUND)
                        return
                    item = adjust_item(item["id"], parse_float(data.get("delta"), -1), data.get("note") or f"tag:{tag_id}")
                    self.send_json({"item": item})
                    return

        self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)


if __name__ == "__main__":
    init_db()
    start_home_assistant_event_listener()
    print(f"{APP_NAME} v{APP_VERSION} ({APP_CODENAME}) starter på port {PORT}. Database: {DB_PATH}", flush=True)
    ThreadingHTTPServer(("", PORT), Handler).serve_forever()
