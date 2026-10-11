"""SQLite access helpers.

A single connection is opened per request and closed on teardown. Rows are
returned as sqlite3.Row so templates can use row["field"] or row.field-style
access through dict().
"""
import os
import sqlite3
from datetime import datetime

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


def get_db():
    if "db" not in g:
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
