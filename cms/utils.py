"""Small shared helpers: money, dates, slugs, YouTube, uploads, CSRF."""
import io
import json
import os
import re
import secrets
import string
from datetime import date, datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

from flask import abort, current_app, request, session
from PIL import Image, ImageOps

Image.MAX_IMAGE_PIXELS = 40_000_000  # refuse "decompression bomb" images

SAST = timezone(timedelta(hours=2))

# ---------------------------------------------------------------- money


def rand(cents, decimals=False):
    """Format cents as South African rand, e.g. 125000 -> 'R1 250'."""
    cents = int(cents or 0)
    value = cents / 100
    if decimals or cents % 100:
        text = f"{value:,.2f}"
    else:
        text = f"{value:,.0f}"
    return "R" + text.replace(",", " ")


def parse_rand(text, default=0):
    """Parse '1 250', 'R1,250.50' or '1250' into cents. Returns default on failure."""
    if text is None:
        return default
    cleaned = re.sub(r"[^\d.]", "", str(text).replace(",", ""))
    if not cleaned:
        return default
    try:
        return int(round(float(cleaned) * 100))
    except ValueError:
        return default


# ---------------------------------------------------------------- dates


def today_sast():
    return datetime.now(SAST).date()


def parse_date(text):
    try:
        return date.fromisoformat(str(text)[:10])
    except (TypeError, ValueError):
        return None


def days_left(deadline):
    d = parse_date(deadline)
    if not d:
        return 0
    return (d - today_sast()).days


def nice_date(text):
    d = parse_date(text)
    return d.strftime("%-d %b %Y") if d else ""


def nice_datetime(text):
    if not text:
        return ""
    try:
        dt = datetime.fromisoformat(str(text)).replace(tzinfo=timezone.utc).astimezone(SAST)
        return dt.strftime("%-d %b %Y, %H:%M")
    except ValueError:
        return str(text)


# ---------------------------------------------------------------- ids & slugs


def make_ref():
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "CMS-" + "".join(secrets.choice(alphabet) for _ in range(5))


def make_token():
    return secrets.token_urlsafe(24)


def slugify(text):
    text = text.lower()
    text = re.sub(r"[éèêë]", "e", text)
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text[:60] or "project"


# ---------------------------------------------------------------- YouTube

_YT_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")


def youtube_id(url):
    """Extract an 11-char YouTube video id from any common URL form (or a bare id)."""
    if not url:
        return None
    url = url.strip()
    if _YT_ID.match(url):
        return url
    try:
        parsed = urlparse(url if "://" in url else "https://" + url)
    except ValueError:
        return None
    host = (parsed.hostname or "").lower().removeprefix("www.").removeprefix("m.")
    candidate = None
    if host == "youtu.be":
        candidate = parsed.path.lstrip("/").split("/")[0]
    elif host in ("youtube.com", "youtube-nocookie.com", "music.youtube.com"):
        if parsed.path == "/watch":
            candidate = parse_qs(parsed.query).get("v", [None])[0]
        else:
            parts = [p for p in parsed.path.split("/") if p]
            if len(parts) >= 2 and parts[0] in ("embed", "shorts", "live", "v"):
                candidate = parts[1]
    return candidate if candidate and _YT_ID.match(candidate) else None


# ---------------------------------------------------------------- uploads

ALLOWED_IMAGE_EXT = {"jpg", "jpeg", "png", "webp", "gif"}


def save_image(file_storage, max_side=1800, prefix="img"):
    """Validate, resize and re-encode an uploaded image. Returns the stored file name.

    Re-encoding with Pillow strips EXIF (including GPS location) and rejects files
    that are not real images, whatever their extension claims.
    """
    if not file_storage or not file_storage.filename:
        return None
    ext = file_storage.filename.rsplit(".", 1)[-1].lower() if "." in file_storage.filename else ""
    if ext not in ALLOWED_IMAGE_EXT:
        raise ValueError("Please upload a JPG, PNG, WEBP or GIF image.")
    try:
        img = Image.open(file_storage.stream)
        img = ImageOps.exif_transpose(img)
    except Exception as exc:  # noqa: BLE001 - Pillow raises many types (incl. DecompressionBombError)
        raise ValueError("That file doesn't look like a valid image, or it's too large.") from exc
    keep_alpha = img.mode in ("RGBA", "LA", "P") and ext in ("png", "webp", "gif")
    img = img.convert("RGBA" if keep_alpha else "RGB")
    img.thumbnail((max_side, max_side))
    name = f"{prefix}-{secrets.token_hex(8)}." + ("png" if keep_alpha else "jpg")
    buf = io.BytesIO()
    if keep_alpha:
        img.save(buf, "PNG", optimize=True)
    else:
        img.save(buf, "JPEG", quality=82, optimize=True, progressive=True)
    from .db import store_media
    store_media(name, "image/png" if keep_alpha else "image/jpeg", buf.getvalue())
    return name


def delete_upload(name):
    if not name:
        return
    from .db import delete_media
    delete_media(os.path.basename(name))
    path = os.path.join(current_app.config["UPLOAD_FOLDER"], os.path.basename(name))
    if os.path.exists(path):
        os.remove(path)


# ---------------------------------------------------------------- CSRF


def csrf_token():
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_urlsafe(32)
    return session["_csrf"]


def check_csrf():
    sent = request.form.get("_csrf") or request.headers.get("X-CSRF-Token")
    if not sent or not secrets.compare_digest(sent, session.get("_csrf", "")):
        abort(400, "Your session expired. Please go back, refresh the page and try again.")


# ---------------------------------------------------------------- misc


def load_json(text, default=None):
    try:
        return json.loads(text or "")
    except (TypeError, ValueError):
        return default if default is not None else []


def parse_line_items(text):
    """Parse admin textarea lines 'Description | 1500' into [{'label','cents'}]."""
    items = []
    for line in (text or "").splitlines():
        if not line.strip():
            continue
        if "|" in line:
            label, amount = line.rsplit("|", 1)
            items.append({"label": label.strip(), "cents": parse_rand(amount)})
        else:
            items.append({"label": line.strip(), "cents": 0})
    return items


def line_items_text(items):
    return "\n".join(
        f"{i['label']} | {i['cents'] / 100:.0f}" if i.get("cents") else i["label"] for i in items
    )


_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def clean_text(value, max_len=200, multiline=False):
    """Trim, cap length and strip control characters from user input."""
    text = _CTRL.sub("", str(value or ""))
    if not multiline:
        text = " ".join(text.split())
    return text.strip()[:max_len]


def safe_url(url, allow_relative=False):
    """Allow only http(s) links; anything else (javascript:, data:) becomes empty.

    With allow_relative, links within this site such as /request or /projects are kept too.
    """
    url = (url or "").strip()
    if not url:
        return ""
    if allow_relative and re.match(r"^/(?!/)[^\s\\]*$", url):
        return url[:300]
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:(?!\d)", url) and not url.lower().startswith(("http://", "https://")):
        return ""  # javascript:, data:, mailto: etc.
    if not url.lower().startswith(("http://", "https://")):
        url = "https://" + url
    parsed = urlparse(url)
    return url[:300] if parsed.scheme in ("http", "https") and parsed.netloc and " " not in url else ""


EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def valid_email(text):
    return bool(text and EMAIL_RE.match(text.strip()))


def initials(name):
    parts = [p for p in re.split(r"\s+", name or "") if p and p[0] in string.ascii_letters]
    return "".join(p[0].upper() for p in parts[:2]) or "★"


def photo_url(image, small=False):
    """URL for a story/gallery photo: either a built-in static file ("static:img/…") or an upload."""
    from flask import current_app, url_for
    image = str(image or "")
    if image.startswith("static:"):
        path = image[len("static:"):]
        if small:
            root, dot, ext = path.rpartition(".")
            alt = f"{root}-sm.{ext}"
            if os.path.exists(os.path.join(current_app.static_folder, alt)):
                path = alt
        return url_for("static", filename=path)
    return url_for("static", filename=f"uploads/{image}")
