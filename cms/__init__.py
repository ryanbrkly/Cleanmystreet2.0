"""Clean My Street — community-funded street cleaning platform."""
import logging
import os

import click
from dotenv import load_dotenv
from flask import Flask, render_template, request
from werkzeug.middleware.proxy_fix import ProxyFix

from . import db, utils


def create_app(test_config=None):
    load_dotenv()
    app = Flask(__name__, instance_relative_config=True,
                static_folder="../static", template_folder="../templates")

    env = os.environ.get("APP_ENV", "development")
    app.config.from_mapping(
        APP_ENV=env,
        SECRET_KEY=os.environ.get("SECRET_KEY", "dev-only-change-me"),
        DATABASE=os.environ.get("DATABASE_PATH", os.path.join(app.instance_path, "cleanmystreet.sqlite3")),
        UPLOAD_FOLDER=os.environ.get("UPLOAD_FOLDER",
                                     os.path.join(os.path.dirname(app.root_path), "static", "uploads")),
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
        SESSION_COOKIE_SECURE=env == "production",
        PERMANENT_SESSION_LIFETIME=60 * 60 * 12,
    )
    if test_config:
        app.config.update(test_config)

    if env == "production":
        if app.config["SECRET_KEY"] == "dev-only-change-me":
            raise RuntimeError("Set SECRET_KEY before running in production.")
        if app.config["PAYMENT_MODE"] == "demo":
            raise RuntimeError("PAYMENT_MODE=demo is not allowed in production. Use PAYMENT_MODE=yoco.")

    os.makedirs(app.instance_path, exist_ok=True)
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    logging.basicConfig(level=logging.INFO)

    app.teardown_appcontext(db.close_db)

    from .admin import bp as admin_bp
    from .public import bp as public_bp

    app.register_blueprint(public_bp)
    app.register_blueprint(admin_bp)

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
        return resp

    @app.errorhandler(404)
    def not_found(_e):
        return render_template("public/error.html", code=404,
                               title="Page not found",
                               message="We couldn't find that page. It may have moved."), 404

    @app.errorhandler(400)
    def bad_request(e):
        return render_template("public/error.html", code=400, title="Something went wrong",
                               message=getattr(e, "description", "Bad request")), 400

    @app.errorhandler(413)
    def too_large(_e):
        return render_template("public/error.html", code=413, title="File too large",
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
