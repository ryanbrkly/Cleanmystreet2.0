# Clean My Street — web app

Community-funded street cleaning. Residents request a clean, the team quotes it, neighbours fund it through Yoco, and the crew posts progress photos and video. If a street misses its goal, every contributor is refunded automatically.

Stack: Python 3.11+ · Flask · SQLite · Jinja templates · plain CSS/JS (no build step). Yoco Checkout API for payments.

---

## Quick start (local)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # then set APP_ENV=development and PAYMENT_MODE=demo for local work
export FLASK_APP=wsgi
flask init-db
flask create-admin              # prompts for email, name, password
flask seed-demo                 # optional: sample projects, updates and sponsor slots
flask run --debug               # http://127.0.0.1:5000  ·  admin at /admin
python -m unittest discover tests -v   # 18 end-to-end tests
```

In **demo** payment mode, a striped banner is shown and checkout is a local page with "simulate success / decline" buttons. Demo mode refuses to start when `APP_ENV=production`.

---

## What's in it

### Public site
| Page | What it does |
|---|---|
| `/` | Hero with live funding card, live stats (computed from the database), how it works, live projects, **homepage YouTube video** (click-to-load), real crew photo gallery with lightbox, mission, **sponsor showcase**, CTA |
| `/projects` | Search + filter (raising funds / funded & in progress / completed / all) |
| `/projects/<slug>` | **Status bar** (Requested → Approved → Funding → Funded → Cleaning → Completed), funding progress, itemised quote, contribution form with preset amounts and **minimum contribution enforced**, WhatsApp / copy / email share, **photo + YouTube progress feed** filterable by before/during/after, backers list, monthly rounds |
| `/request` | Request form with photo upload and once-off / monthly / unsure preference |
| `/track/<token>` | Requester's private status page; becomes an edit form when the team asks for revisions |
| `/how-it-works`, `/about`, `/sponsors`, `/privacy`, `/terms` | Content pages (privacy and terms are **drafts — have them reviewed**) |
| `/sitemap.xml`, `/robots.txt` | SEO. Every page is server-rendered with title, description and Open Graph tags, so WhatsApp link previews and Google work |

### Admin portal (`/admin`)
- **Dashboard** — requests to review, projects to schedule, money raised, refunds needing attention, live projects with funding bars, recent contributions, activity log.
- **Requests** — tabs for to-review / waiting on requester / approved / rejected. Each request has:
  - **Quote builder** — line items (`Description | amount`), **once-off price** and **monthly upkeep price**, public scope.
  - **Approve & publish** — choose once-off or monthly tier, set **funding goal**, **overall budget** (internal cost), **minimum contribution** per household, and funding window. Creates the project and emails the funding link.
  - **Request revisions** — message is shown on the requester's tracking page, which turns into an edit form.
  - **Reject** — with a reason sent to the requester.
  - Internal note (team only).
- **Projects** — edit goal, budget, minimum, deadline, scheduled date, summary, scope, line items and cover photo; one-click status moves (funded → scheduled → cleaning → completed, emailing backers); **upload multiple progress photos** tagged before/during/after; **add YouTube videos** by URL; written updates with optional email to all backers; per-contribution refunds; **cancel & refund everyone**; **open next month's round** for monthly projects.
- **Payments** — ledger with filters and CSV export.
- **Sponsors** — add (logo, name, link), edit, hide/show, **reorder** (↑/↓), delete. Homepage shows them in this order.
- **Site settings** — **homepage YouTube video URL** + heading/caption, announcement bar, contact email/phone/WhatsApp, service areas, default minimum contribution and funding window, and "work done before this platform" figures added to the live stats.

---

## Payments (Yoco)

1. In the Yoco portal, copy your **secret key** (`sk_test_…` first, `sk_live_…` when ready).
2. Set `PAYMENT_MODE=yoco`, `YOCO_SECRET_KEY`, `PUBLIC_BASE_URL` in `.env`.
3. Register the webhook once your site is on HTTPS:
   ```bash
   flask register-webhook https://www.cleanmystreet.co.za/webhooks/yoco
   ```
   Copy the `secret` (`whsec_…`) it prints into `YOCO_WEBHOOK_SECRET` and restart.
4. Do a full test with `sk_test_` keys and Yoco's test cards: contribute, check the project updates, then refund from the admin.

How it works in code (`cms/payments.py`):
- Contribution saved as `pending` → `POST /api/checkouts` (amount in cents, ZAR, success/cancel/failure URLs, metadata, idempotency key) → redirect to Yoco.
- Payment confirmed by **either** the signed webhook (`payment.succeeded`) **or** the return page reading `GET /api/checkouts/{id}` — whichever is first. Both are idempotent, and webhook IDs are stored to stop replays.
- Webhook signatures are verified (HMAC-SHA256, `webhook-id`/`webhook-timestamp`/`webhook-signature` headers, 3-minute tolerance); anything unsigned gets a 401.
- When paid total ≥ goal the project flips to **Funded** automatically and backers are emailed.
- Refunds use `POST /api/checkouts/{id}/refund`. Failed refunds are marked "refund pending", shown on the dashboard, and retried daily.

> The Yoco request/response field names follow Yoco's published Checkout API. Confirm them against the current docs at developer.yoco.com during your sandbox test — the unit tests mock Yoco, so a real sandbox run is the final check.

**Monthly upkeep:** Yoco Checkout takes one-off payments, so monthly projects run as monthly funding rounds: when a month is done, the admin clicks "Open next month's round" and shares the new link. If you later want automatic debit orders, that needs a provider with recurring billing (e.g. PayFast subscriptions).

---

## Daily job: deadlines and refunds

Projects that pass their deadline without reaching the goal are closed and every contributor is refunded. Run once a day, either:

```bash
flask process-expired                                   # server cron
curl -X POST "https://www.cleanmystreet.co.za/tasks/process-expired?key=$CRON_SECRET"   # external cron (e.g. cron-job.org)
```

It also runs whenever an admin opens the dashboard, as a safety net.

---

## Deploying

Any host that runs Python works (Render, Railway, Fly.io, a VPS at Afrihost/Hetzner/xneelo).

- Start command: `gunicorn wsgi:app --workers 2` (see `Procfile`).
- **Persistent disk:** the SQLite file (`DATABASE_PATH`) and `static/uploads/` must be on a disk that survives deploys.
- Put it behind HTTPS (the host usually does this). Set `APP_ENV=production` — this enables secure cookies and blocks demo payments.
- Email: set the `SMTP_*` variables (Google Workspace, Microsoft 365, Mailgun, SES…). Without them, emails are written to the log.
- Point `cleanmystreet.co.za` and `www` at the host. Note the old site's footer used **info@cleanmystreets.co.za** (with an "s"); this build uses **info@cleanmystreet.co.za** — make sure that mailbox exists.
- Back up the database file and uploads folder daily.

### Before launch checklist
- [ ] `flask create-admin` for each team member; don't run `seed-demo` on the live database
- [ ] Yoco sandbox test end to end, then switch to live keys
- [ ] Webhook registered and `YOCO_WEBHOOK_SECRET` set
- [ ] Daily cron for `process-expired`
- [ ] SMTP configured and a test request received
- [ ] Privacy policy and terms reviewed by a South African legal adviser (POPIA)
- [ ] Homepage video URL and real sponsors added in Admin → Site settings / Sponsors
- [ ] Any figures from before this platform entered in Site settings only if you can back them up

---

## Project layout

```
cms/
  __init__.py     app factory, config, CLI commands
  schema.sql      database schema
  db.py           SQLite helpers, settings, activity log
  services.py     domain logic: totals, funding, refunds, monthly rounds, expiry
  payments.py     Yoco client + webhook verification
  public.py       public routes, payments, webhook
  admin.py        admin portal routes
  notify.py       email (SMTP or log)
  utils.py        money, dates, YouTube parsing, image uploads, CSRF
  seed.py         demo data
templates/        public/, admin/, partials/, email/
static/           css/site.css, js/site.js, img/ (logo + crew photos), uploads/
tests/            end-to-end tests (unittest)
```

Security notes: CSRF tokens on every form, sessions rotated at sign-in, login rate limiting, uploads re-encoded with Pillow (strips EXIF/GPS, rejects non-images), webhook signature + replay protection, security headers, admin pages `noindex`.
