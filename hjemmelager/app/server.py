#!/usr/bin/env python3
import html
import json
import os
import sqlite3
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse


APP_NAME = "Hjemmelager"
APP_VERSION = "0.1.1"
APP_CODENAME = "Første hylle"
DATA_DIR = Path(os.environ.get("HJEMMELAGER_DATA_DIR", "./data"))
DB_PATH = DATA_DIR / "hjemmelager.db"
PORT = int(os.environ.get("HJEMMELAGER_PORT", "8099"))


def now():
    return int(time.time())


def db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db() as conn:
        conn.executescript(
            """
            create table if not exists items (
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
            """
        )


def row_to_item(row):
    item = dict(row)
    item["is_low"] = (
        item["kind"] == "consumable"
        and item["shopping_enabled"] == 1
        and item["min_quantity"] > 0
        and item["quantity"] <= item["min_quantity"]
    )
    return item


def list_items(where="", params=()):
    query = "select * from items"
    if where:
        query += f" where {where}"
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


def get_item(item_id):
    with db() as conn:
        row = conn.execute("select * from items where id = ?", (item_id,)).fetchone()
    return row_to_item(row) if row else None


def get_item_by_tag(tag_id):
    with db() as conn:
        row = conn.execute("select * from items where tag_id = ?", (tag_id,)).fetchone()
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
    quantity = parse_float(data.get("quantity"))
    with db() as conn:
        cur = conn.execute(
            """
            insert into items (
                name, kind, quantity, unit, min_quantity, location, category, tag_id,
                image_url, note, shopping_enabled, created_at, updated_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                (data.get("name") or "Uten navn").strip(),
                data.get("kind") or "consumable",
                quantity,
                (data.get("unit") or "stk").strip(),
                parse_float(data.get("min_quantity")),
                (data.get("location") or "").strip(),
                (data.get("category") or "").strip(),
                tag_id,
                (data.get("image_url") or "").strip(),
                (data.get("note") or "").strip(),
                1 if str(data.get("shopping_enabled", "1")).lower() in ("1", "true", "on", "yes") else 0,
                timestamp,
                timestamp,
            ),
        )
        item_id = cur.lastrowid
        save_event(conn, item_id, "created", None, quantity)
    return get_item(item_id)


def update_item(item_id, data):
    existing = get_item(item_id)
    if not existing:
        return None
    timestamp = now()
    tag_id = (data.get("tag_id") or "").strip() or None
    with db() as conn:
        conn.execute(
            """
            update items set
                name = ?, kind = ?, quantity = ?, unit = ?, min_quantity = ?,
                location = ?, category = ?, tag_id = ?, image_url = ?, note = ?,
                shopping_enabled = ?, updated_at = ?
            where id = ?
            """,
            (
                (data.get("name") or existing["name"]).strip(),
                data.get("kind") or existing["kind"],
                parse_float(data.get("quantity"), existing["quantity"]),
                (data.get("unit") or existing["unit"]).strip(),
                parse_float(data.get("min_quantity"), existing["min_quantity"]),
                (data.get("location") or "").strip(),
                (data.get("category") or "").strip(),
                tag_id,
                (data.get("image_url") or "").strip(),
                (data.get("note") or "").strip(),
                1 if str(data.get("shopping_enabled", "1")).lower() in ("1", "true", "on", "yes") else 0,
                timestamp,
                item_id,
            ),
        )
        save_event(conn, item_id, "updated", None, parse_float(data.get("quantity"), existing["quantity"]))
    return get_item(item_id)


def adjust_item(item_id, delta, note=""):
    with db() as conn:
        row = conn.execute("select * from items where id = ?", (item_id,)).fetchone()
        if not row:
            return None
        quantity = max(0, float(row["quantity"]) + float(delta))
        conn.execute(
            "update items set quantity = ?, updated_at = ? where id = ?",
            (quantity, now(), item_id),
        )
        save_event(conn, item_id, "adjusted", delta, quantity, note)
    return get_item(item_id)


def touch_tag(tag_id):
    item = get_item_by_tag(tag_id)
    if not item:
        return None
    with db() as conn:
        conn.execute(
            "update items set last_scanned_at = ?, updated_at = ? where id = ?",
            (now(), now(), item["id"]),
        )
        save_event(conn, item["id"], "tag_scanned", None, item["quantity"], tag_id)
    return get_item(item["id"])


def page(title, body, base_path=""):
    base = esc(base_path.rstrip("/") + "/" if base_path else "")
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
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font: 16px/1.45 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    header {{
      position: sticky;
      top: 0;
      z-index: 2;
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
      font-size: 1.1rem;
      color: var(--text);
      text-decoration: none;
    }}
    nav {{
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      justify-content: flex-end;
    }}
    a, button {{ touch-action: manipulation; }}
    .nav, .btn {{
      border: 1px solid var(--line);
      border-radius: 8px;
      color: var(--text);
      background: var(--panel);
      padding: 8px 11px;
      text-decoration: none;
      font-weight: 650;
      cursor: pointer;
    }}
    .btn.primary, .nav.primary {{
      background: var(--accent);
      border-color: var(--accent);
      color: white;
    }}
    .btn.warn {{ color: white; background: var(--accent-2); border-color: var(--accent-2); }}
    .btn.danger {{ color: white; background: var(--danger); border-color: var(--danger); }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(250px, 1fr));
      gap: 12px;
    }}
    .card {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 14px;
    }}
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
    .low {{ border-color: #f59e0b; color: #92400e; background: #fef3c7; }}
    .qty {{ font-size: 2rem; font-weight: 800; margin: 8px 0; }}
    .actions {{ display: flex; gap: 8px; flex-wrap: wrap; margin-top: 12px; }}
    form.stack, .stack {{ display: grid; gap: 12px; }}
    label {{ display: grid; gap: 5px; font-weight: 650; }}
    input, select, textarea {{
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 8px;
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
    .full {{ grid-column: 1 / -1; }}
    table {{ width: 100%; border-collapse: collapse; background: var(--panel); border-radius: 8px; overflow: hidden; }}
    th, td {{ padding: 10px; border-bottom: 1px solid var(--line); text-align: left; }}
    @media (max-width: 680px) {{
      .bar {{ align-items: flex-start; flex-direction: column; }}
      nav {{ justify-content: flex-start; }}
      .form-grid {{ grid-template-columns: 1fr; }}
      .qty {{ font-size: 1.7rem; }}
    }}
  </style>
</head>
<body>
  <header>
    <div class="bar">
      <a class="brand" href=".">{APP_NAME}</a>
      <nav>
        <a class="nav" href=".">Varer</a>
        <a class="nav" href="low-stock">Lav beholdning</a>
        <a class="nav primary" href="new">Ny</a>
      </nav>
    </div>
  </header>
  <main>{body}</main>
  <footer class="bar muted" style="padding-top: 24px; padding-bottom: 24px;">
    {APP_NAME} v{APP_VERSION} · Kodenavn {APP_CODENAME}
  </footer>
</body>
</html>"""


def item_card(item):
    low = '<span class="pill low">Lav</span>' if item["is_low"] else ""
    meta = " · ".join(filter(None, [item["category"], item["location"]]))
    kind = "Forbruksvare" if item["kind"] == "consumable" else "Gjenstand"
    return f"""
    <article class="card">
      <div class="item-title">
        <h2><a href="item/{item['id']}">{esc(item['name'])}</a></h2>
        {low}
      </div>
      <div class="muted">{esc(kind)}{(" · " + esc(meta)) if meta else ""}</div>
      <div class="qty">{fmt_num(item['quantity'])} <span class="muted">{esc(item['unit'])}</span></div>
      <div class="actions">
        <form method="post" action="item/{item['id']}/adjust"><input type="hidden" name="delta" value="-1"><button class="btn" title="Ta ut en">-1</button></form>
        <form method="post" action="item/{item['id']}/adjust"><input type="hidden" name="delta" value="1"><button class="btn" title="Legg til en">+1</button></form>
        <a class="btn" href="item/{item['id']}">Åpne</a>
      </div>
    </article>
    """


def item_form(item=None, tag_id=""):
    item = item or {
        "id": None,
        "name": "",
        "kind": "consumable",
        "quantity": 0,
        "unit": "stk",
        "min_quantity": 0,
        "location": "",
        "category": "",
        "tag_id": tag_id,
        "image_url": "",
        "note": "",
        "shopping_enabled": 1,
    }
    action = f"item/{item['id']}/edit" if item["id"] else "new"
    checked = "checked" if item["shopping_enabled"] else ""
    return f"""
    <form class="stack" method="post" action="{action}">
      <div class="form-grid">
        <label class="full">Navn
          <input name="name" value="{esc(item['name'])}" required>
        </label>
        <label>Type
          <select name="kind">
            <option value="consumable" {"selected" if item['kind'] == 'consumable' else ""}>Forbruksvare</option>
            <option value="thing" {"selected" if item['kind'] == 'thing' else ""}>Gjenstand</option>
          </select>
        </label>
        <label>Kategori
          <input name="category" value="{esc(item['category'])}" placeholder="Batterier, kabler, mat">
        </label>
        <label>Antall
          <input name="quantity" type="number" step="0.01" value="{fmt_num(item['quantity'])}">
        </label>
        <label>Enhet
          <input name="unit" value="{esc(item['unit'])}" placeholder="stk, pk, meter">
        </label>
        <label>Minimum
          <input name="min_quantity" type="number" step="0.01" value="{fmt_num(item['min_quantity'])}">
        </label>
        <label>Plassering
          <input name="location" value="{esc(item['location'])}" placeholder="Bod > Hylle 2 > Boks A">
        </label>
        <label class="full">NFC tag-id
          <input name="tag_id" value="{esc(item['tag_id'] or '')}" placeholder="tag_id fra Home Assistant">
        </label>
        <label class="full">Bilde-URL
          <input name="image_url" value="{esc(item['image_url'])}" placeholder="valgfritt">
        </label>
        <label class="full">Notat
          <textarea name="note">{esc(item['note'])}</textarea>
        </label>
        <label class="full">
          <span><input type="checkbox" name="shopping_enabled" value="1" {checked}> Vis som lav beholdning når antall er under minimum</span>
        </label>
      </div>
      <div class="actions">
        <button class="btn primary">Lagre</button>
        <a class="btn" href=".">Avbryt</a>
      </div>
    </form>
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

    def redirect(self, target):
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", self.ingress_base() + "/" + target.lstrip("/"))
        self.end_headers()

    def do_GET(self):
        path = self.route_path()
        if path in ("", "items"):
            query = parse_qs(urlparse(self.path).query)
            search = (query.get("q") or [""])[0].strip()
            if search:
                items = list_items(
                    "name like ? or location like ? or category like ? or tag_id like ?",
                    tuple([f"%{search}%"] * 4),
                )
            else:
                items = list_items()
            cards = "".join(item_card(item) for item in items) or '<div class="card">Ingen varer ennå.</div>'
            body = f"""
              <h1>Varer og ting</h1>
              <form method="get" action="." class="stack" style="margin-bottom: 14px;">
                <input name="q" value="{esc(search)}" placeholder="Søk etter navn, plassering, kategori eller tag">
              </form>
              <section class="grid">{cards}</section>
            """
            self.send_html("Varer", body)
            return

        if path == "new":
            tag_id = (parse_qs(urlparse(self.path).query).get("tag_id") or [""])[0]
            self.send_html("Ny vare", f"<h1>Ny vare</h1>{item_form(tag_id=tag_id)}")
            return

        if path == "low-stock":
            items = list_items("kind = 'consumable' and shopping_enabled = 1 and min_quantity > 0 and quantity <= min_quantity")
            cards = "".join(item_card(item) for item in items) or '<div class="card">Ingen lave beholdninger akkurat nå.</div>'
            self.send_html("Lav beholdning", f"<h1>Lav beholdning</h1><section class=\"grid\">{cards}</section>")
            return

        if path.startswith("item/"):
            parts = path.split("/")
            if len(parts) == 2 and parts[1].isdigit():
                item = get_item(int(parts[1]))
                if not item:
                    self.send_html("Ikke funnet", "<h1>Ikke funnet</h1>", HTTPStatus.NOT_FOUND)
                    return
                img = f'<img src="{esc(item["image_url"])}" alt="" style="max-width: 100%; border-radius: 8px; margin-bottom: 12px;">' if item["image_url"] else ""
                low = '<span class="pill low">Lav beholdning</span>' if item["is_low"] else ""
                body = f"""
                  <div class="card">
                    {img}
                    <div class="item-title"><h1>{esc(item['name'])}</h1>{low}</div>
                    <div class="qty">{fmt_num(item['quantity'])} <span class="muted">{esc(item['unit'])}</span></div>
                    <p class="muted">{esc(item['category'])} {("· " + esc(item['location'])) if item['location'] else ""}</p>
                    <p>{esc(item['note'])}</p>
                    <div class="actions">
                      <form method="post" action="item/{item['id']}/adjust"><input type="hidden" name="delta" value="-1"><button class="btn warn">-1</button></form>
                      <form method="post" action="item/{item['id']}/adjust"><input type="hidden" name="delta" value="1"><button class="btn primary">+1</button></form>
                      <a class="btn" href="item/{item['id']}/edit">Rediger</a>
                    </div>
                  </div>
                """
                self.send_html(item["name"], body)
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
            self.send_json({"error": f"Invalid body: {exc}"}, HTTPStatus.BAD_REQUEST)
            return

        if path == "new":
            try:
                item = create_item(data)
            except sqlite3.IntegrityError:
                self.send_html("Tag finnes", "<h1>Tag-id er allerede i bruk</h1>", HTTPStatus.CONFLICT)
                return
            self.redirect(f"item/{item['id']}")
            return

        if path == "api/items":
            try:
                item = create_item(data)
            except sqlite3.IntegrityError:
                self.send_json({"error": "tag_id already exists"}, HTTPStatus.CONFLICT)
                return
            self.send_json({"item": item}, HTTPStatus.CREATED)
            return

        if path.startswith("item/"):
            parts = path.split("/")
            if len(parts) == 3 and parts[2] == "adjust" and parts[1].isdigit():
                adjust_item(int(parts[1]), parse_float(data.get("delta")), "web")
                self.redirect(f"item/{parts[1]}")
                return
            if len(parts) == 3 and parts[2] == "edit" and parts[1].isdigit():
                try:
                    item = update_item(int(parts[1]), data)
                except sqlite3.IntegrityError:
                    self.send_html("Tag finnes", "<h1>Tag-id er allerede i bruk</h1>", HTTPStatus.CONFLICT)
                    return
                if not item:
                    self.send_html("Ikke funnet", "<h1>Ikke funnet</h1>", HTTPStatus.NOT_FOUND)
                    return
                self.redirect(f"item/{item['id']}")
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

        if path.startswith("api/tag/"):
            parts = path.split("/")
            if len(parts) >= 4:
                tag_id = parts[2]
                action = parts[3]
                if action == "touch":
                    item = touch_tag(tag_id)
                    if not item:
                        self.send_json({"error": "tag not found", "tag_id": tag_id, "create_path": f"new?tag_id={tag_id}"}, HTTPStatus.NOT_FOUND)
                        return
                    self.send_json({"item": item})
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
    print(f"{APP_NAME} v{APP_VERSION} ({APP_CODENAME}) starter på port {PORT}. Database: {DB_PATH}", flush=True)
    ThreadingHTTPServer(("", PORT), Handler).serve_forever()
