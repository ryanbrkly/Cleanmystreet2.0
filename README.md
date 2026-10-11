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

## Emails

Every email the site sends is listed in **Admin → Emails**, grouped by who receives it:

- **Requester:** request received, changes received, more details needed, quote approved (funding link), declined, deadline reminder (3 days before, once), project closed.
- **Backers and the requester:** contribution receipt, fully funded, clean scheduled, crew on site, completed, project updates (when you tick “Email backers and the requester”), refunds.
- **Team:** new request and project funded alerts, sent to the addresses under “Team alerts” (or `ADMIN_NOTIFY_EMAIL`).

Each email has an editable subject and HTML body, plus a shared header/footer layout. The editor shows a live preview with sample details, lists the placeholders each email can use (`{{ name }}`, `{{ track_url }}`, `{{ project_url }}` …), and can send you a test. Templates are rendered in a sandbox and values are escaped, so an edited template can’t run code on the server. **Reset to the original** restores the default. Each email can be turned off.

Every attempt is recorded in the email log (Admin → Emails, and on each request and project page) as Sent, Not sent (SMTP not configured), Failed or Turned off.

Set `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD` and `MAIL_FROM` to deliver mail. Optionally set `SITE_URL` (e.g. `https://cleanmystreet.co.za`) for links in emails sent from the command line.

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

### Vercel (demo only)
Vercel picks up `wsgi.py` automatically, so no `vercel.json` is needed. Don't name any folder `public` (Vercel strips it from the app). Set these environment variables in Vercel → Settings → Environment Variables, then redeploy:

| Variable | Value |
|---|---|
| `SECRET_KEY` | a long random string |
| `ADMIN_EMAIL` / `ADMIN_PASSWORD` | your admin sign-in (created automatically) |
| `PAYMENT_MODE` | `demo` (leave `APP_ENV` unset) |

**Vercel has no permanent disk.** The app stores its database and uploads in `/tmp`, which Vercel wipes whenever it restarts the function (often within minutes), and each copy of the function has its own data. On Vercel the app therefore reloads the sample projects on every fresh start. Use it to show people the site, not to take real requests or payments.

For the real site use a host with a persistent disk (below), or ask for the Postgres + file-storage version for Vercel.

### Permanent storage on Vercel (Turso)
Without a database, Vercel keeps data in temporary storage that is wiped on every restart, and each copy of the app has its own. Requests then "disappear" and links break. Connect a free Turso database (hosted SQLite) to fix this:

1. In Vercel, open the project → **Storage** → **Create Database** → choose **Turso** (or create one at turso.tech).
2. Connect it to this project. Vercel adds `TURSO_DATABASE_URL` and `TURSO_AUTH_TOKEN` to the environment variables. If you created it on turso.tech, add those two yourself (database URL from `turso db show`, token from `turso db tokens create`).
3. Set `SEED_DEMO=0` if you want to start with an empty site, or `1` to load the sample projects once.
4. Redeploy. Tables are created automatically on the first visit. Uploaded photos are stored in the database too.

The admin portal shows a red warning while data is still temporary.

### Persistent hosts (recommended for the live site)

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
  notify.py       email delivery (SMTP or log) and the email log
  emails.py       editable email templates, defaults and sandboxed rendering
  utils.py        money, dates, YouTube parsing, image uploads, CSRF
  seed.py         demo data
templates/        public/, admin/, partials/, email/
static/           css/site.css, js/site.js, img/ (logo + crew photos), uploads/
tests/            end-to-end tests (unittest)
```

Security notes: CSRF tokens on every form, sessions rotated at sign-in, login rate limiting, uploads re-encoded with Pillow (strips EXIF/GPS, rejects non-images), webhook signature + replay protection, security headers, admin pages `noindex`.
