"""Database access.

Two back ends, same SQL (SQLite dialect):

- Local SQLite file (default). Good for a VPS or any host with a permanent disk.
- Turso (hosted, SQLite-compatible) over HTTPS, when TURSO_DATABASE_URL is set.
  Use this on Vercel and other serverless hosts, whose disks are wiped on restart.
  Uses the Hrana-over-HTTP "pipeline" endpoint, so no extra Python packages.

Rows behave like sqlite3.Row: row["col"], dict(row), and attribute-style access
in templates.
"""
import base64
import os
import sqlite3
from collections.abc import Mapping
from datetime import datetime

import requests
from flask import current_app, g

DEFAULT_SETTINGS = {
    "hero_video_url": "",
    "video_heading": "See a Clean My Street crew at work",
    "video_caption": "From overgrown verges to clear drains — one street, one morning.",
    "contact_email": "info@cleanmystreet.co.za",
    "contact_phone": "",
    "whatsapp_number": "",
    "default_min_contribution_cents": "5000",
    "default_funding_days": "21",
    "legacy_streets": "0",
    "legacy_raised_cents": "0",
    "legacy_contributors": "0",
    "announcement": "",
    "service_areas": "Johannesburg, Pretoria, Cape Town, Durban",
}


# ---------------------------------------------------------------- Turso (Hrana over HTTP)


class DatabaseError(Exception):
    pass


class Row(Mapping):
    """Read-only row compatible with sqlite3.Row usage in this app."""

    __slots__ = ("_cols", "_vals")

    def __init__(self, cols, vals):
        self._cols = cols
        self._vals = vals

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._vals[key]
        try:
            return self._vals[self._cols.index(key)]
        except ValueError:
            raise KeyError(key) from None

    def __iter__(self):
        return iter(self._cols)

    def __len__(self):
        return len(self._cols)

    def keys(self):
        return list(self._cols)


def _encode(value):
    if value is None:
        return {"type": "null"}
    if isinstance(value, bool):
        return {"type": "integer", "value": str(int(value))}
    if isinstance(value, int):
        return {"type": "integer", "value": str(value)}
    if isinstance(value, float):
        return {"type": "float", "value": value}
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"type": "blob", "base64": base64.b64encode(bytes(value)).decode()}
    return {"type": "text", "value": str(value)}


def _decode(cell):
    kind = cell.get("type")
    if kind == "null":
        return None
    if kind == "integer":
        return int(cell["value"])
    if kind == "float":
        return float(cell["value"])
    if kind == "blob":
        return base64.b64decode(cell.get("base64", ""))
    return cell.get("value")


class _Cursor:
    def __init__(self, rows, lastrowid):
        self._rows = rows
        self.lastrowid = lastrowid

    def fetchall(self):
        return self._rows

    def close(self):
        pass


class TursoConnection:
    """Minimal connection object with the subset of the sqlite3 API this app uses."""

    def __init__(self, url, token):
        url = url.strip()
        if url.startswith("libsql://"):
            url = "https://" + url[len("libsql://"):]
        self.endpoint = url.rstrip("/") + "/v2/pipeline"
        self.session = requests.Session()
        self.session.headers.update({"Authorization": f"Bearer {token}", "Content-Type": "application/json"})

    def _pipeline(self, statements):
        reqs = [{"type": "execute", "stmt": {"sql": sql, "args": [_encode(a) for a in args]}}
                for sql, args in statements]
        reqs.append({"type": "close"})
        try:
            resp = self.session.post(self.endpoint, json={"requests": reqs}, timeout=20)
        except requests.RequestException as exc:
            raise DatabaseError(f"Could not reach the database: {exc}") from exc
        if resp.status_code >= 400:
            raise DatabaseError(f"Database returned HTTP {resp.status_code}: {resp.text[:300]}")
        out = []
        for item in resp.json().get("results", [])[:-1]:
            if item.get("type") != "ok":
                raise DatabaseError((item.get("error") or {}).get("message", "Database error"))
            out.append(item["response"]["result"])
        return out

    def execute(self, sql, args=()):
        result = self._pipeline([(sql, tuple(args))])[0]
        cols = [c.get("name") for c in result.get("cols", [])]
        rows = [Row(cols, [_decode(c) for c in r]) for r in result.get("rows", [])]
        last = result.get("last_insert_rowid")
        return _Cursor(rows, int(last) if last not in (None, "") else None)

    def executescript(self, script):
        statements = []
        for chunk in script.split(";"):
            sql = "\n".join(line for line in chunk.splitlines() if not line.strip().startswith("--")).strip()
            if sql and not sql.upper().startswith("PRAGMA"):
                statements.append((sql, ()))
        if statements:
            self._pipeline(statements)

    def commit(self):
        pass  # each pipeline is committed by the server

    def close(self):
        self.session.close()


# ---------------------------------------------------------------- connection management


def using_turso():
    return bool(current_app.config.get("TURSO_DATABASE_URL"))


def get_db():
    if "db" not in g:
        if using_turso():
            g.db = TursoConnection(current_app.config["TURSO_DATABASE_URL"],
                                   current_app.config.get("TURSO_AUTH_TOKEN", ""))
        else:
            path = current_app.config["DATABASE"]
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            conn = sqlite3.connect(path, detect_types=sqlite3.PARSE_DECLTYPES)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA journal_mode = WAL")
            g.db = conn
    return g.db


def close_db(_exc=None):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def init_db():
    conn = get_db()
    with current_app.open_resource("schema.sql") as fh:
        conn.executescript(fh.read().decode("utf-8"))
    for key, value in DEFAULT_SETTINGS.items():
        conn.execute("INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)", (key, value))
    conn.commit()


def query(sql, args=(), one=False):
    cur = get_db().execute(sql, args)
    rows = cur.fetchall()
    cur.close()
    return (rows[0] if rows else None) if one else rows


def execute(sql, args=()):
    conn = get_db()
    cur = conn.execute(sql, args)
    conn.commit()
    return cur.lastrowid


# ---------------------------------------------------------------- media (uploaded photos)
# Photos are stored in the database so they survive restarts on any host.


def store_media(name, mime, data):
    execute("INSERT OR REPLACE INTO media(name, mime, data) VALUES (?, ?, ?)", (name, mime, data))


def load_media(name):
    return query("SELECT mime, data FROM media WHERE name=?", (name,), one=True)


def delete_media(name):
    execute("DELETE FROM media WHERE name=?", (name,))


# ---------------------------------------------------------------- settings & activity


def get_settings():
    rows = query("SELECT key, value FROM settings")
    data = dict(DEFAULT_SETTINGS)
    data.update({r["key"]: r["value"] for r in rows})
    return data


def set_setting(key, value):
    execute(
        "INSERT INTO settings(key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def log_activity(action, detail="", admin_email=None):
    if admin_email is None:
        admin_email = g.get("admin_email", "system") if g else "system"
    execute(
        "INSERT INTO activity(admin_email, action, detail) VALUES (?, ?, ?)",
        (admin_email, action, detail[:500]),
    )


def now_iso():
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
