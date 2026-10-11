"""Clean My Street — community-funded street cleaning platform."""
import logging
import os

import click
from dotenv import load_dotenv
from flask import Flask, render_template, request
from werkzeug.middleware.proxy_fix import ProxyFix

from . import db, utils


CSP = ("default-src 'self'; "
       "img-src 'self' data: https://i.ytimg.com; "
       "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
       "font-src 'self' https://fonts.gstatic.com; "
       "script-src 'self'; "
       "frame-src https://www.youtube-nocookie.com; "
       "form-action 'self' https://*.yoco.com https://yoco.com; "
       "base-uri 'self'; frame-ancestors 'self'")


def create_app(test_config=None):
    load_dotenv()
    app = Flask(__name__, instance_relative_config=True,
                static_folder="../static", template_folder="../templates")

    env = os.environ.get("APP_ENV", "development")
    # Serverless hosts (Vercel, AWS Lambda) only allow writing to /tmp, and /tmp is
    # wiped whenever the function restarts. Data there is temporary: fine for a demo,
    # not for real projects and payments (see README, "Deploying").
    serverless = bool(os.environ.get("VERCEL") or os.environ.get("VERCEL_ENV") or os.environ.get("VERCEL_URL")
                      or os.environ.get("AWS_LAMBDA_FUNCTION_NAME") or os.environ.get("LAMBDA_TASK_ROOT"))
    if not serverless and not os.environ.get("DATABASE_PATH"):
        # Read-only project folder (some serverless hosts): fall back to /tmp automatically.
        try:
            os.makedirs(app.instance_path, exist_ok=True)
            probe = os.path.join(app.instance_path, ".write-test")
            open(probe, "w").close()
            os.remove(probe)
        except OSError:
            serverless = True
    default_db = "/tmp/cleanmystreet.sqlite3" if serverless else os.path.join(app.instance_path, "cleanmystreet.sqlite3")
    default_uploads = "/tmp/uploads" if serverless else os.path.join(os.path.dirname(app.root_path), "static", "uploads")
    app.config.from_mapping(
        APP_ENV=env,
        SERVERLESS=serverless,
        SEED_DEMO=os.environ.get("SEED_DEMO", "1" if serverless else "0") == "1",
        SECRET_KEY=os.environ.get("SECRET_KEY", "dev-only-change-me"),
        DATABASE=os.environ.get("DATABASE_PATH", default_db),
        UPLOAD_FOLDER=os.environ.get("UPLOAD_FOLDER", default_uploads),
        MAX_CONTENT_LENGTH=25 * 1024 * 1024,
        PUBLIC_BASE_URL=os.environ.get("PUBLIC_BASE_URL", "").rstrip("/"),
        PAYMENT_MODE=os.environ.get("PAYMENT_MODE", "demo"),
        YOCO_SECRET_KEY=os.environ.get("YOCO_SECRET_KEY", ""),
        YOCO_WEBHOOK_SECRET=os.environ.get("YOCO_WEBHOOK_SECRET", ""),
        CRON_SECRET=os.environ.get("CRON_SECRET", ""),
        SMTP_HOST=os.environ.get("SMTP_HOST", ""),
        SMTP_PORT=os.environ.get("SMTP_PORT", "587"),
        SMTP_USER=os.environ.get("SMTP_USER", ""),
        SMTP_PASSWORD=os.environ.get("SMTP_PASSWORD", ""),
        MAIL_FROM=os.environ.get("MAIL_FROM", "Clean My Street <info@cleanmystreet.co.za>"),
        MAIL_REPLY_TO=os.environ.get("MAIL_REPLY_TO", ""),
        ADMIN_NOTIFY_EMAIL=os.environ.get("ADMIN_NOTIFY_EMAIL", ""),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=env == "production" or serverless,
        PERMANENT_SESSION_LIFETIME=60 * 60 * 12,
    )
    if test_config:
        app.config.update(test_config)

    # Misconfiguration shows a clear page instead of crashing the whole app.
    config_errors = []
    if env == "production":
        if app.config["SECRET_KEY"] == "dev-only-change-me":
            config_errors.append("SECRET_KEY is not set.")
        if app.config["PAYMENT_MODE"] == "demo":
            config_errors.append("PAYMENT_MODE is 'demo'. Set PAYMENT_MODE=yoco and your Yoco keys.")
        if app.config["PAYMENT_MODE"] == "yoco" and not app.config["YOCO_SECRET_KEY"]:
            config_errors.append("YOCO_SECRET_KEY is not set.")
    app.config["CONFIG_ERRORS"] = config_errors

    for folder in (os.path.dirname(app.config["DATABASE"]), app.config["UPLOAD_FOLDER"]):
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError:
            logging.getLogger(__name__).warning("Could not create %s (read-only filesystem?)", folder)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    logging.basicConfig(level=logging.INFO)

    app.teardown_appcontext(db.close_db)

    from .admin import bp as admin_bp
    from .public import bp as public_bp

    app.register_blueprint(public_bp)
    app.register_blueprint(admin_bp)

    # Uploaded files live in UPLOAD_FOLDER, which may be outside static/ (e.g. /tmp on Vercel).
    from flask import send_from_directory

    @app.route("/static/uploads/<path:filename>")
    def uploaded_file(filename):
        return send_from_directory(app.config["UPLOAD_FOLDER"], filename, max_age=86400)

    @app.before_request
    def _startup_checks():
        if app.config["CONFIG_ERRORS"] and request.endpoint not in ("static", "public.healthz"):
            items = "".join(f"<li>{e}</li>" for e in app.config["CONFIG_ERRORS"])
            return (f"<!doctype html><title>Setup needed</title><body style='font-family:system-ui;padding:40px;max-width:640px'>"
                    f"<h1>Clean My Street needs a few settings</h1><p>Add these environment variables in your host's "
                    f"settings, then redeploy:</p><ul>{items}</ul></body>", 503)
        if not app.config.get("_DB_READY"):
            _ensure_database(app)

    # ------------------------------------------------------------ templates
    app.jinja_env.filters.update(
        rand=utils.rand,
        nice_date=utils.nice_date,
        nice_datetime=utils.nice_datetime,
        initials=utils.initials,
    )

    @app.context_processor
    def inject_globals():
        from .services import PHASES
        try:
            settings = db.get_settings()
        except Exception:  # noqa: BLE001 - before init-db
            settings = dict(db.DEFAULT_SETTINGS)
        return {
            "csrf_token": utils.csrf_token,
            "site": settings,
            "PHASES": PHASES,
            "payment_mode": app.config["PAYMENT_MODE"],
            "base_url": app.config["PUBLIC_BASE_URL"] or request.host_url.rstrip("/"),
            "now_year": utils.today_sast().year,
        }

    @app.after_request
    def security_headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        resp.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        resp.headers.setdefault("Content-Security-Policy", CSP)
        if request.is_secure:
            resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        if request.path.startswith("/admin"):
            resp.headers.setdefault("Cache-Control", "no-store")
        return resp

    @app.errorhandler(500)
    def server_error(_e):
        try:
            return render_template("pages/error.html", code=500, title="Something went wrong on our side",
                                   message="Please try again in a minute. If it keeps happening, email us."), 500
        except Exception:  # noqa: BLE001 - never fail while reporting a failure
            return "Something went wrong. Please try again in a minute.", 500

    @app.errorhandler(404)
    def not_found(_e):
        return render_template("pages/error.html", code=404,
                               title="Page not found",
                               message="We couldn't find that page. It may have moved."), 404

    @app.errorhandler(400)
    def bad_request(e):
        return render_template("pages/error.html", code=400, title="Something went wrong",
                               message=getattr(e, "description", "Bad request")), 400

    @app.errorhandler(413)
    def too_large(_e):
        return render_template("pages/error.html", code=413, title="File too large",
                               message="Please upload images smaller than 25 MB."), 413

    # ------------------------------------------------------------ CLI
    @app.cli.command("init-db")
    def init_db_cmd():
        """Create tables and default settings (safe to re-run)."""
        db.init_db()
        click.echo("Database ready.")

    @app.cli.command("create-admin")
    @click.option("--email", prompt=True)
    @click.option("--name", prompt=True, default="Admin")
    @click.password_option()
    def create_admin_cmd(email, name, password):
        """Create or reset an admin account."""
        from werkzeug.security import generate_password_hash
        db.init_db()
        db.execute(
            "INSERT INTO admins(email, name, password_hash) VALUES (?,?,?) "
            "ON CONFLICT(email) DO UPDATE SET password_hash=excluded.password_hash, name=excluded.name",
            (email.strip().lower(), name, generate_password_hash(password)),
        )
        click.echo(f"Admin {email} saved.")

    @app.cli.command("process-expired")
    def process_expired_cmd():
        """Close projects past their deadline and refund backers (run daily via cron)."""
        from .services import process_expired_projects
        for title, outcome in process_expired_projects():
            click.echo(f"{title}: {outcome}")
        click.echo("Done.")

    @app.cli.command("register-webhook")
    @click.argument("url")
    def register_webhook_cmd(url):
        """Register https://<your-domain>/webhooks/yoco with Yoco and print the signing secret."""
        from .payments import register_webhook
        data = register_webhook(url)
        click.echo(data)
        click.echo("Put the 'secret' value into YOCO_WEBHOOK_SECRET and restart the app.")

    @app.cli.command("seed-demo")
    def seed_demo_cmd():
        """Load sample projects, updates and sponsor slots for previews."""
        from .seed import seed_demo
        seed_demo()
        click.echo("Demo data loaded.")

    return app


def _ensure_database(app):
    """Create tables on first use; on serverless hosts also load demo data and an admin."""
    from .db import query
    try:
        query("SELECT 1 FROM projects LIMIT 1")
    except Exception:  # noqa: BLE001 - table missing on a fresh database
        db.init_db()
        if app.config["SEED_DEMO"]:
            from .seed import seed_demo
            seed_demo()
    email = os.environ.get("ADMIN_EMAIL", "").strip().lower()
    password = os.environ.get("ADMIN_PASSWORD", "")
    if email and password and not query("SELECT 1 FROM admins WHERE email=?", (email,), one=True):
        from werkzeug.security import generate_password_hash
        db.execute("INSERT INTO admins(email, name, password_hash) VALUES (?,?,?)",
                   (email, os.environ.get("ADMIN_NAME", "Admin"), generate_password_hash(password)))
    app.config["_DB_READY"] = True
