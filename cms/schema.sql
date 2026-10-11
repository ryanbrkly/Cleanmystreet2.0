-- Clean My Street — database schema (SQLite).
-- All money values are stored as integer cents (ZAR) to avoid rounding errors.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS admins (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    email         TEXT NOT NULL UNIQUE,
    name          TEXT NOT NULL DEFAULT '',
    password_hash TEXT NOT NULL,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

-- A resident's request to have a street cleaned.
-- status: submitted | under_review | revision_requested | approved | rejected
CREATE TABLE IF NOT EXISTS requests (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ref             TEXT NOT NULL UNIQUE,          -- human reference, e.g. CMS-7K2Q
    token           TEXT NOT NULL UNIQUE,          -- secret for the requester's status/edit link
    street_name     TEXT NOT NULL,
    suburb          TEXT NOT NULL DEFAULT '',
    city            TEXT NOT NULL,
    postcode        TEXT NOT NULL,
    description     TEXT NOT NULL,
    service_pref    TEXT NOT NULL DEFAULT 'once',  -- once | monthly | unsure
    funding_days    INTEGER NOT NULL DEFAULT 21,
    photo           TEXT,                          -- uploaded file name
    requester_name  TEXT NOT NULL,
    email           TEXT NOT NULL,
    phone           TEXT NOT NULL DEFAULT '',
    consent         INTEGER NOT NULL DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'submitted',
    admin_note      TEXT NOT NULL DEFAULT '',      -- message shown to requester (revision / rejection reason)
    internal_note   TEXT NOT NULL DEFAULT '',      -- never shown publicly
    project_id      INTEGER REFERENCES projects(id),
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- A quote prepared by an admin for a request. One-off and/or monthly tiers.
CREATE TABLE IF NOT EXISTS quotes (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id         INTEGER NOT NULL REFERENCES requests(id) ON DELETE CASCADE,
    once_cents         INTEGER NOT NULL DEFAULT 0,
    monthly_cents      INTEGER NOT NULL DEFAULT 0,
    line_items         TEXT NOT NULL DEFAULT '[]', -- JSON [{"label":..,"cents":..}]
    scope              TEXT NOT NULL DEFAULT '',
    created_at         TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at         TEXT NOT NULL DEFAULT (datetime('now'))
);

-- A public, fundable project.
-- status: funding | funded | scheduled | in_progress | completed | failed | cancelled
CREATE TABLE IF NOT EXISTS projects (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    slug                   TEXT NOT NULL UNIQUE,
    request_id             INTEGER REFERENCES requests(id),
    parent_id              INTEGER REFERENCES projects(id), -- previous monthly round
    round_no               INTEGER NOT NULL DEFAULT 1,
    title                  TEXT NOT NULL,
    street_name            TEXT NOT NULL,
    suburb                 TEXT NOT NULL DEFAULT '',
    city                   TEXT NOT NULL,
    summary                TEXT NOT NULL DEFAULT '',
    scope                  TEXT NOT NULL DEFAULT '',
    service_type           TEXT NOT NULL DEFAULT 'once',   -- once | monthly
    line_items             TEXT NOT NULL DEFAULT '[]',
    budget_cents           INTEGER NOT NULL DEFAULT 0,     -- internal overall budget (cost to deliver)
    goal_cents             INTEGER NOT NULL,               -- public funding target
    min_contribution_cents INTEGER NOT NULL DEFAULT 5000,
    deadline               TEXT NOT NULL,                  -- ISO date (end of day, SAST)
    status                 TEXT NOT NULL DEFAULT 'funding',
    cover_image            TEXT,
    scheduled_for          TEXT,
    funded_at              TEXT,
    completed_at           TEXT,
    closed_reason          TEXT NOT NULL DEFAULT '',
    created_at             TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at             TEXT NOT NULL DEFAULT (datetime('now'))
);

-- A single contribution (one Yoco checkout).
-- status: pending | paid | failed | refund_pending | refunded
CREATE TABLE IF NOT EXISTS contributions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id    INTEGER NOT NULL REFERENCES projects(id),
    name          TEXT NOT NULL,
    email         TEXT NOT NULL,
    anonymous     INTEGER NOT NULL DEFAULT 0,
    message       TEXT NOT NULL DEFAULT '',
    amount_cents  INTEGER NOT NULL,
    status        TEXT NOT NULL DEFAULT 'pending',
    checkout_id   TEXT UNIQUE,
    payment_id    TEXT,
    refund_id     TEXT,
    return_token  TEXT NOT NULL,
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    paid_at       TEXT,
    refunded_at   TEXT
);

-- Progress feed items on a project page.
-- kind: photo | video | note ; phase: before | during | after | general
CREATE TABLE IF NOT EXISTS updates (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    kind        TEXT NOT NULL,
    phase       TEXT NOT NULL DEFAULT 'general',
    title       TEXT NOT NULL DEFAULT '',
    body        TEXT NOT NULL DEFAULT '',
    image       TEXT,
    youtube_id  TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS sponsors (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    logo        TEXT,
    url         TEXT NOT NULL DEFAULT '',
    blurb       TEXT NOT NULL DEFAULT '',
    sort_order  INTEGER NOT NULL DEFAULT 0,
    active      INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Raw webhook deliveries, for auditing and replay protection.
CREATE TABLE IF NOT EXISTS webhook_events (
    id           TEXT PRIMARY KEY,   -- webhook-id header
    type         TEXT NOT NULL,
    payload      TEXT NOT NULL,
    received_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Admin activity log.
CREATE TABLE IF NOT EXISTS activity (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    admin_email TEXT NOT NULL DEFAULT 'system',
    action      TEXT NOT NULL,
    detail      TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_requests_status ON requests(status);
CREATE INDEX IF NOT EXISTS idx_projects_status ON projects(status);
CREATE INDEX IF NOT EXISTS idx_contrib_project ON contributions(project_id, status);
CREATE INDEX IF NOT EXISTS idx_updates_project ON updates(project_id);
