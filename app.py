import html as html_escape
import json
import logging
import mimetypes
import os
import re
import secrets
import smtplib
import ssl
import uuid
from urllib.parse import urlencode, urlparse
from functools import wraps
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, Response, abort, flash, jsonify, redirect, render_template, request, send_from_directory, session, url_for
from flask_compress import Compress
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import safe_join, secure_filename
import requests


# -----------------------------------------------------------------------------
# Environment / Setup
# -----------------------------------------------------------------------------
load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
STATIC_DIR = BASE_DIR / "static"
PRODUCTS_FILE = DATA_DIR / "products.json"
FILES_DIR = DATA_DIR / "files"
UPLOADS_DIR = DATA_DIR / "uploads"
MEDIA_FILE = DATA_DIR / "media.json"
ORDERS_FILE = DATA_DIR / "orders.json"
SUPPORT_TICKETS_FILE = DATA_DIR / "support_tickets.json"
SUPPORT_UPLOADS_DIR = DATA_DIR / "support_uploads"
SETTINGS_FILE = DATA_DIR / "site_settings.json"
DISCOUNT_CODE_MAX_LEN = 32
ALLOWED_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}
ALLOWED_FILE_EXTENSIONS = {"zip", "rar", "7z", "pdf", "txt", "json", "png", "jpg", "jpeg", "gif", "webp"}
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "30"))
MAX_SUPPORT_ATTACHMENT_MB = max(1, min(15, int(os.getenv("MAX_SUPPORT_ATTACHMENT_MB", "8"))))
MAX_SUPPORT_ATTACHMENTS_PER_MESSAGE = max(1, min(5, int(os.getenv("MAX_SUPPORT_ATTACHMENTS_PER_MESSAGE", "3"))))

FLASK_ENV = os.getenv("FLASK_ENV", "development").lower()
IS_PRODUCTION = FLASK_ENV == "production"


# -----------------------------------------------------------------------------
# Database / MongoDB
# -----------------------------------------------------------------------------
MONGO_URI = os.getenv("MONGO_URI", "").strip()
MONGO_DB_NAME = os.getenv("MONGO_DB_NAME", "moealturej_services")
OWNER_EMAIL = os.getenv("OWNER_EMAIL", "owner@moealturej.com").strip().lower()
OWNER_USERNAME = os.getenv("OWNER_USERNAME", "owner").strip() or "owner"
OWNER_PASSWORD = os.getenv("OWNER_PASSWORD", "changeme-owner-password")
# Email delivery
# Production email is sent through Resend over HTTPS. The sender MUST be a
# verified moealturej.com address. Gmail is only used as Reply-To.
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "").strip()
EMAIL_PROVIDER = os.getenv("EMAIL_PROVIDER", "resend").strip().lower() or "resend"
RESEND_REPLY_TO = os.getenv("RESEND_REPLY_TO", os.getenv("EMAIL_REPLY_TO", "moealturej@gmail.com")).strip()
EMAIL_FROM_NAME = os.getenv("EMAIL_FROM_NAME", f"{os.getenv('APP_NAME', 'moealturej')} Security").strip()

# Backwards-compatible main sender. If your old env has EMAIL_FROM_EMAIL it will
# still work, but the default is now a verified domain sender instead of Gmail.
EMAIL_FROM_EMAIL = os.getenv("EMAIL_FROM_EMAIL", os.getenv("RESEND_FROM_EMAIL", "security@moealturej.com")).strip()
SECURITY_FROM_EMAIL = os.getenv("SECURITY_FROM_EMAIL", "Moealturej Security <security@moealturej.com>").strip()
ORDERS_FROM_EMAIL = os.getenv("ORDERS_FROM_EMAIL", "Moealturej Orders <orders@moealturej.com>").strip()
SUPPORT_FROM_EMAIL = os.getenv("SUPPORT_FROM_EMAIL", "Moealturej Support <support@moealturej.com>").strip()
DOWNLOADS_FROM_EMAIL = os.getenv("DOWNLOADS_FROM_EMAIL", "Moealturej Downloads <downloads@moealturej.com>").strip()
NOTIFICATIONS_FROM_EMAIL = os.getenv("NOTIFICATIONS_FROM_EMAIL", "Moealturej <no-reply@moealturej.com>").strip()

def _sender_address_from_config(value: str) -> str:
    match = re.search(r"<([^>]+)>", value or "")
    return (match.group(1) if match else value or "").strip().lower()

def _force_domain_sender(value: str, fallback: str) -> str:
    address = _sender_address_from_config(value)
    if address.endswith("@gmail.com") or (address and not address.endswith("@moealturej.com")):
        return fallback
    return value or fallback

EMAIL_FROM_EMAIL = _force_domain_sender(EMAIL_FROM_EMAIL, "security@moealturej.com")
SECURITY_FROM_EMAIL = _force_domain_sender(SECURITY_FROM_EMAIL, "Moealturej Security <security@moealturej.com>")
ORDERS_FROM_EMAIL = _force_domain_sender(ORDERS_FROM_EMAIL, "Moealturej Orders <orders@moealturej.com>")
SUPPORT_FROM_EMAIL = _force_domain_sender(SUPPORT_FROM_EMAIL, "Moealturej Support <support@moealturej.com>")
DOWNLOADS_FROM_EMAIL = _force_domain_sender(DOWNLOADS_FROM_EMAIL, "Moealturej Downloads <downloads@moealturej.com>")
NOTIFICATIONS_FROM_EMAIL = _force_domain_sender(NOTIFICATIONS_FROM_EMAIL, "Moealturej <no-reply@moealturej.com>")

# Optional SMTP is disabled unless you explicitly set EMAIL_PROVIDER=smtp. Render
# should use Resend, not SMTP ports.
SMTP_EMAIL = os.getenv("SMTP_EMAIL", "").strip()
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "").strip()
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com").strip()
SMTP_PORT = int(os.getenv("SMTP_PORT", "465"))
REQUIRE_EMAIL_CODES = os.getenv("REQUIRE_EMAIL_CODES", "true").lower() == "true"
def env_first(*names: str, default: str = "") -> str:
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return default

DISCORD_CLIENT_ID = env_first("DISCORD_CLIENT_ID", "DISCORD_APPLICATION_ID", "DISCORD_APP_ID")
DISCORD_CLIENT_SECRET = env_first("DISCORD_CLIENT_SECRET", "DISCORD_OAUTH_CLIENT_SECRET", "DISCORD_SECRET", "DISCORD_APP_SECRET")
DISCORD_REDIRECT_URI = env_first("DISCORD_REDIRECT_URI", "DISCORD_REDIRECT_URL")
DISCORD_API_BASE = "https://discord.com/api/v10"
DISCORD_BOT_TOKEN = env_first("DISCORD_BOT_TOKEN", "BOT_TOKEN", "TOKEN")  # Optional: only needed for guild joins/server actions.
DISCORD_GUILD_ID = env_first("DISCORD_GUILD_ID", "DISCORD_SERVER_ID", default="1224469092606410762")
DISCORD_AUTO_JOIN_GUILD = os.getenv("DISCORD_AUTO_JOIN_GUILD", "true").lower() == "true"

GOOGLE_CLIENT_ID = env_first("GOOGLE_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_ID")
GOOGLE_CLIENT_SECRET = env_first("GOOGLE_CLIENT_SECRET", "GOOGLE_OAUTH_CLIENT_SECRET")
GOOGLE_REDIRECT_URI = env_first("GOOGLE_REDIRECT_URI", "GOOGLE_REDIRECT_URL")
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"

STRIPE_SECRET_KEY = env_first("STRIPE_SECRET_KEY", "STRIPE_API_KEY")
STRIPE_WEBHOOK_SECRET = env_first("STRIPE_WEBHOOK_SECRET", "STRIPE_ENDPOINT_SECRET")
STRIPE_CURRENCY = os.getenv("STRIPE_CURRENCY", "usd").strip().lower() or "usd"
PAYPAL_CLIENT_ID = env_first("PAYPAL_CLIENT_ID", "PAYPAL_SANDBOX_CLIENT_ID")
PAYPAL_CLIENT_SECRET = env_first("PAYPAL_CLIENT_SECRET", "PAYPAL_SANDBOX_CLIENT_SECRET")
PAYPAL_MODE = os.getenv("PAYPAL_MODE", "sandbox").strip().lower()
PAYPAL_API_BASE = "https://api-m.paypal.com" if PAYPAL_MODE == "live" else "https://api-m.sandbox.paypal.com"
PROCESSING_FEE_PERCENT = max(0, min(100, int(os.getenv("PROCESSING_FEE_PERCENT", "10"))))
CHECKOUT_REQUIRE_LOGIN = os.getenv("CHECKOUT_REQUIRE_LOGIN", "true").lower() == "true"
PENDING_ORDER_MAX_AGE_MINUTES = max(1, int(os.getenv("PENDING_ORDER_MAX_AGE_MINUTES", "10")))
APP_NAME = os.getenv("APP_NAME", "moealturej").strip() or "moealturej"
APP_URL = os.getenv("APP_URL", "https://moealturej.com").strip().rstrip("/")
SUPPORT_EMAIL = os.getenv("SUPPORT_EMAIL", RESEND_REPLY_TO or OWNER_EMAIL).strip()
BRAND_LOGO_URL = os.getenv("BRAND_LOGO_URL", "").strip()
OWNER_ORDER_WEBHOOK_URL = env_first("OWNER_ORDER_WEBHOOK_URL", "ORDER_WEBHOOK_URL", "DISCORD_ORDER_WEBHOOK_URL")
SUPPORT_TICKET_WEBHOOK_URL = env_first("SUPPORT_TICKET_WEBHOOK_URL", "DISCORD_SUPPORT_WEBHOOK_URL", default="https://discord.com/api/webhooks/1367237331735678986/bGwY8l7JXSksdnys1erTvQ1e7kKv08p7C7Z0odUwuRcx4E3EDlgj3vosmTZMywyDRyso")
DELIVERY_DM_ENABLED = os.getenv("DELIVERY_DM_ENABLED", "true").lower() == "true"

# Reselling.pro auto key delivery. Keep the API key in .env only. Product/option
# JSON stores the provider base URL WITHOUT the key, for example:
# https://api.reselling.pro/rft/api/seller/keys/mw19ghostinternal/1day
RESELLING_PRO_API_KEY = env_first("RESELLING_PRO_API_KEY", "RESELLING_PRO_TOKEN")
RESELLING_PRO_ENABLED = os.getenv("RESELLING_PRO_ENABLED", "true").lower() == "true"
RESELLING_PRO_TIMEOUT_SECONDS = max(3, min(30, int(os.getenv("RESELLING_PRO_TIMEOUT_SECONDS", "15"))))
RESELLING_PRO_ALLOWED_HOST = os.getenv("RESELLING_PRO_ALLOWED_HOST", "api.reselling.pro").strip().lower()

def utc_now() -> datetime:
    return datetime.now(timezone.utc)

def utc_now_naive() -> datetime:
    return utc_now().replace(tzinfo=None)

def today_utc_date() -> str:
    return utc_now().strftime("%Y-%m-%d")

mongo_client = None
db = None
mongo_status_reason = "Not initialized yet."
products_col = None
users_col = None
settings_col = None
media_col = None
audit_col = None
orders_col = None
support_tickets_col = None
media_fs = None

def init_mongo():
    global mongo_client, db, mongo_status_reason, products_col, users_col, settings_col, media_col, audit_col, orders_col, support_tickets_col, media_fs
    if not MONGO_URI:
        mongo_status_reason = "MONGO_URI is empty in .env, so the app is using local JSON fallback."
        logger.warning("MONGO_URI is not set. Using local JSON fallback for products and local-only owner login.")
        return
    placeholder_hosts = ("cluster.mongodb.net", "your-cluster", "your_mongo", "example.com")
    if any(host in MONGO_URI.lower() for host in placeholder_hosts):
        mongo_status_reason = "MONGO_URI still looks like a placeholder Atlas URI. Paste the real connection string from MongoDB Atlas."
        logger.warning("MONGO_URI looks like a placeholder (%s). Using local JSON fallback until you paste your real Atlas connection string.", MONGO_URI)
        return
    try:
        from pymongo import MongoClient, ASCENDING
        import gridfs
        mongo_client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
        mongo_client.admin.command("ping")
        db = mongo_client[MONGO_DB_NAME]
        products_col = db["products"]
        users_col = db["users"]
        settings_col = db["settings"]
        media_col = db["media"]
        audit_col = db["audit_logs"]
        orders_col = db["orders"]
        support_tickets_col = db["support_tickets"]
        media_fs = gridfs.GridFS(db, collection="media_files")
        users_col.create_index([("email", ASCENDING)], unique=True)
        products_col.create_index([("slug", ASCENDING)], unique=True)
        media_col.create_index([("filename", ASCENDING)], unique=True)
        audit_col.create_index([("created_at", ASCENDING)])
        orders_col.create_index([("order_id", ASCENDING)], unique=True)
        orders_col.create_index([("user_email", ASCENDING)])
        support_tickets_col.create_index([("ticket_id", ASCENDING)], unique=True)
        support_tickets_col.create_index([("user_email", ASCENDING)])
        support_tickets_col.create_index([("status", ASCENDING), ("updated_at", ASCENDING)])
        mongo_status_reason = f"Connected to MongoDB database {MONGO_DB_NAME}."
        logger.info("Connected to MongoDB database %s", MONGO_DB_NAME)
    except Exception as exc:
        mongo_status_reason = f"MongoDB connection failed: {exc}. Check MONGO_URI, username/password, IP whitelist, and your Atlas cluster hostname."
        logger.error("MongoDB connection failed (%s). Falling back to local JSON mode. Check MONGO_URI in .env.", exc)
        mongo_client = db = products_col = users_col = settings_col = media_col = audit_col = orders_col = support_tickets_col = media_fs = None
media_col = None
audit_col = None
orders_col = None
support_tickets_col = None
media_fs = None

def using_mongo() -> bool:
    return products_col is not None and users_col is not None

def normalize_product(product: dict) -> dict:
    if not isinstance(product, dict):
        product = {}
    product.pop("_id", None)
    if not product.get("id"):
        product["id"] = int(utc_now().timestamp() * 1000)
    if not product.get("slug"):
        product["slug"] = str(product.get("name", "untitled-product")).lower().replace(" ", "-")
    product.setdefault("name", "Untitled Product")
    product.setdefault("image", "/static/logo.png")
    product.setdefault("category", "general")
    product.setdefault("type", "product")
    try:
        product["displayOrder"] = int(product.get("displayOrder", product.get("sortOrder", product.get("order", 9999))))
    except (TypeError, ValueError):
        product["displayOrder"] = 9999
    product.setdefault("featured", False)
    product.setdefault("features", [])
    product.setdefault("store", {"enabled": True, "stockStatus": "In Stock", "options": []})
    product.setdefault("downloads", {"enabled": False, "version": "Latest", "downloadUrl": "", "fileSize": ""})
    product.setdefault("status", {"enabled": True, "state": "Operational", "label": "Online", "lastUpdated": today_utc_date()})
    return product

def seed_products_if_needed():
    if not using_mongo() or products_col.count_documents({}) > 0:
        return
    products = []
    if PRODUCTS_FILE.exists():
        try:
            with PRODUCTS_FILE.open("r", encoding="utf-8") as f:
                products = json.load(f)
        except Exception:
            logger.exception("Could not seed MongoDB from products.json")
    products = [normalize_product(p) for p in products if isinstance(p, dict)]
    for idx, product in enumerate(products):
        product.setdefault("displayOrder", idx)
    if products:
        products_col.insert_many(products)
        logger.info("Seeded %d products into MongoDB", len(products))

def ensure_owner_account():
    if not using_mongo():
        return
    existing = users_col.find_one({"email": OWNER_EMAIL})
    payload = {
        "email": OWNER_EMAIL,
        "username": OWNER_USERNAME,
        "role": "owner",
        "is_owner": True,
        "updated_at": utc_now_naive(),
    }
    if existing:
        users_col.update_one({"_id": existing["_id"]}, {"$set": payload})
        return
    payload.update({
        "password_hash": generate_password_hash(OWNER_PASSWORD),
        "created_at": utc_now_naive(),
    })
    users_col.insert_one(payload)
    logger.warning("Created owner account for %s. Change OWNER_PASSWORD before production.", OWNER_EMAIL)

def save_products_file(products: list):
    DATA_DIR.mkdir(exist_ok=True)
    with PRODUCTS_FILE.open("w", encoding="utf-8") as f:
        json.dump(products, f, indent=2, ensure_ascii=False)

def sanitize_user_for_session(user: dict | None) -> dict | None:
    """Store only safe, JSON-serializable fields in Flask's signed cookie session.

    Mongo user documents can contain ObjectIds, datetimes, password hashes, reset
    tokens, and other fields that should never be written back into the browser
    cookie. The session-refresh security hook uses this helper to keep the
    logged-in user fresh without leaking sensitive data or crashing on Mongo
    types.
    """
    if not user:
        return None
    email = str(user.get("email") or "").strip().lower()
    username = str(user.get("username") or (email.split("@")[0] if email else "user")).strip() or "user"
    role = str(user.get("role") or ("owner" if user.get("is_owner") else "user")).strip().lower()
    is_owner = bool(user.get("is_owner") or role == "owner")
    return {
        "email": email,
        "username": username,
        "is_owner": is_owner,
        "role": "owner" if is_owner else (role if role in {"admin", "support"} else "user"),
        "auth_provider": str(user.get("auth_provider") or "email"),
        "discord_id": str(user.get("discord_id") or "") or None,
        "discord_username": str(user.get("discord_username") or "") or None,
        "discord_avatar": str(user.get("discord_avatar") or "") or None,
        "google_id": str(user.get("google_id") or "") or None,
        "status": str(user.get("status") or "active"),
    }


def current_user():
    return session.get("user")

def has_admin_access(user: dict | None) -> bool:
    return bool(user and (user.get("is_owner") or user.get("role") in {"owner", "admin"}))

def has_support_access(user: dict | None) -> bool:
    return bool(user and (user.get("is_owner") or user.get("role") in {"owner", "admin", "support"}))

def owner_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        user = current_user()
        if not user:
            flash("Log in to access the dashboard.", "warning")
            return redirect(url_for("login", next=request.path))
        if not has_admin_access(user):
            abort(403)
        return view(*args, **kwargs)
    return wrapper

def support_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        user = current_user()
        if not user:
            flash("Log in to access support tickets.", "warning")
            return redirect(url_for("login", next=request.path))
        if not has_support_access(user):
            abort(403)
        return view(*args, **kwargs)
    return wrapper

def login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not current_user():
            flash("Please sign in before checkout.", "warning")
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapper

# -----------------------------------------------------------------------------
# Logging
# -----------------------------------------------------------------------------
log_level = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, log_level, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

init_mongo()
seed_products_if_needed()
ensure_owner_account()

# -----------------------------------------------------------------------------
# Flask App
# -----------------------------------------------------------------------------
app = Flask(__name__, template_folder="templates", static_folder="static")

# Reverse proxy support (Render / Nginx / Cloudflare / etc.)
if os.getenv("BEHIND_PROXY", "false").lower() == "true":
    app.wsgi_app = ProxyFix(
        app.wsgi_app,
        x_for=1,
        x_proto=1,
        x_host=1,
        x_port=1,
        x_prefix=1,
    )

# -----------------------------------------------------------------------------
# Secret key / Sessions
# -----------------------------------------------------------------------------
secret_key = os.getenv("SECRET_KEY")
if not secret_key:
    if IS_PRODUCTION:
        raise RuntimeError("SECRET_KEY environment variable is not set")
    secret_key = f"dev-key-{os.urandom(24).hex()}"
    logger.warning("Using temporary development SECRET_KEY. Set SECRET_KEY for production.")

app.config.update(
    SECRET_KEY=secret_key,
    PERMANENT_SESSION_LIFETIME=timedelta(minutes=20),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=IS_PRODUCTION,
    SESSION_REFRESH_EACH_REQUEST=True,
    JSON_SORT_KEYS=False,
    MAX_CONTENT_LENGTH=MAX_UPLOAD_MB * 1024 * 1024,
    MAX_SUPPORT_ATTACHMENT_MB=MAX_SUPPORT_ATTACHMENT_MB,
    SEND_FILE_MAX_AGE_DEFAULT=31536000 if IS_PRODUCTION else 300,
    SMTP_EMAIL=SMTP_EMAIL,
    EMAIL_PROVIDER=EMAIL_PROVIDER,
    EMAIL_FROM_EMAIL=EMAIL_FROM_EMAIL,
    RESEND_REPLY_TO=RESEND_REPLY_TO,
    SECURITY_FROM_EMAIL=SECURITY_FROM_EMAIL,
    ORDERS_FROM_EMAIL=ORDERS_FROM_EMAIL,
    DOWNLOADS_FROM_EMAIL=DOWNLOADS_FROM_EMAIL,
    SUPPORT_FROM_EMAIL=SUPPORT_FROM_EMAIL,
    RESEND_ENABLED=bool(RESEND_API_KEY),
    APP_URL=APP_URL,
    BRAND_LOGO_URL=BRAND_LOGO_URL,
)

# -----------------------------------------------------------------------------
# Compression
# -----------------------------------------------------------------------------
Compress(app)

# -----------------------------------------------------------------------------
# Rate Limiting
# -----------------------------------------------------------------------------
limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    default_limits=["3000 per day", "500 per hour", "60 per minute"],
    storage_uri=os.getenv("RATELIMIT_STORAGE_URI", "memory://"),
    strategy="fixed-window",
    headers_enabled=True,
)

# -----------------------------------------------------------------------------
# Security Headers
# -----------------------------------------------------------------------------
CSP_POLICY = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://js.stripe.com https://www.paypal.com https://www.paypalobjects.com; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdnjs.cloudflare.com; "
    "img-src 'self' data: https:; "
    "font-src 'self' data: https://fonts.gstatic.com https://cdnjs.cloudflare.com; "
    "connect-src 'self' https://cdn.jsdelivr.net https://api.stripe.com https://checkout.stripe.com https://www.paypal.com https://www.paypalobjects.com https://api-m.paypal.com https://api-m.sandbox.paypal.com https://formspree.io; "
    "frame-src https://js.stripe.com https://checkout.stripe.com https://hooks.stripe.com https://www.paypal.com https://www.paypalobjects.com; "
    "worker-src 'self' blob:; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "form-action 'self' https://checkout.stripe.com https://www.paypal.com; "
    "frame-ancestors 'none';"
)

# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------


def parse_detailed_description(raw: str) -> str:
    """Convert pasted feature-list text into the compact bullet format used by product JSON."""
    raw = str(raw or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.strip() for line in raw.split("\n") if line.strip()]
    if not lines:
        return ""

    known_headings = {
        "aimbot", "targeting", "esp", "visuals", "colors", "misc", "config", "settings",
        "radar", "unlock", "unlocks", "weapon mods", "movement", "self", "vehicle control",
        "player interaction", "vehicle list tools", "teleportation", "server tools", "world mods",
        "triggerbot", "silent aim", "silent aim / magic bullet", "account", "anti aim", "override",
        "exploits", "dvars"
    }

    def next_line(index: int) -> str:
        for nxt in lines[index + 1:]:
            if nxt:
                return nxt
        return ""

    def is_heading(line: str, index: int) -> bool:
        if line.startswith(("-", "•")):
            return False
        clean = line.strip().rstrip(":")
        lower = clean.lower()
        if lower in known_headings:
            return True
        nxt = next_line(index)
        if re.match(r"^save slot\s+\d+", lower):
            return False
        return len(clean) <= 32 and bool(nxt.startswith(("-", "•")))

    notes: list[str] = []
    groups: list[dict] = []
    current: dict | None = None

    for index, line in enumerate(lines):
        if is_heading(line, index):
            current = {"title": line.rstrip(":"), "items": []}
            groups.append(current)
            continue

        item = re.sub(r"^[-•]\s*", "", line).strip()
        if not item:
            continue
        if current is None:
            notes.append(item)
        else:
            current["items"].append(item)

    output: list[str] = []
    if notes:
        output.append("• Notes: " + ", ".join(notes))
    for group in groups:
        items = [str(i).strip() for i in group.get("items", []) if str(i).strip()]
        if items:
            output.append(f"• {group['title']}: " + ", ".join(items))

    return "\n".join(output)


def safe_reselling_base_url(value: str) -> str:
    """Validate/store a Reselling.pro base URL without the secret API key."""
    base_url = str(value or "").strip().rstrip("/")
    if not base_url:
        return ""
    parsed = urlparse(base_url)
    if parsed.scheme != "https" or parsed.netloc.lower() != RESELLING_PRO_ALLOWED_HOST:
        raise ValueError("Auto-delivery URL must start with https://api.reselling.pro")
    if not parsed.path.startswith("/rft/api/seller/keys/"):
        raise ValueError("Auto-delivery URL must use /rft/api/seller/keys/...")
    # Admin should paste only the base URL. If the key got pasted by accident, remove it.
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) > 6:
        clean_path = "/" + "/".join(parts[:6])
        parsed = parsed._replace(path=clean_path, query="", fragment="")
        base_url = parsed.geturl().rstrip("/")
    return base_url


def auto_delivery_dict_from_base_url(base_url: str, enabled: bool = True) -> dict:
    base_url = safe_reselling_base_url(base_url)
    return {"enabled": bool(enabled and base_url), "provider": "reselling_pro", "base_url": base_url}


def build_store_options_from_form() -> list[dict]:
    names = request.form.getlist("option_name")
    prices = request.form.getlist("option_price")
    auto_enabled = request.form.getlist("option_auto_enabled")
    auto_urls = request.form.getlist("option_auto_base_url")
    options: list[dict] = []
    for index, name in enumerate(names):
        option_name = (name or "").strip()
        raw_price = prices[index] if index < len(prices) else ""
        if not option_name and not str(raw_price).strip():
            continue
        if not option_name:
            option_name = f"Option {index + 1}"
        try:
            price = round(float(raw_price), 2)
        except Exception:
            price = 1.00
        if price < 0.50:
            price = 0.50
        option = {
            "id": int(utc_now().timestamp() * 1000) + index,
            "name": option_name,
            "price": price,
        }
        base_url = auto_urls[index].strip() if index < len(auto_urls) else ""
        enabled = str(index) in set(auto_enabled)
        if base_url:
            option["autoDelivery"] = auto_delivery_dict_from_base_url(base_url, enabled)
        options.append(option)
    if not options:
        options.append({"id": int(utc_now().timestamp() * 1000), "name": "DAY KEY", "price": 1.00})
    return options

def clean_slug(value: str) -> str:
    value = str(value or "").strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value).strip("-")
    return value or f"item-{int(utc_now().timestamp())}"


def csrf_token() -> str:
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return token


def verify_csrf() -> bool:
    sent = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token")
    return bool(sent and secrets.compare_digest(sent, session.get("csrf_token", "")))


def email_delivery_ready() -> bool:
    if EMAIL_PROVIDER == "smtp":
        return bool(SMTP_EMAIL and SMTP_PASSWORD)
    return bool(RESEND_API_KEY)


def _extract_email_address(sender: str) -> str:
    match = re.search(r"<([^>]+)>", sender or "")
    return (match.group(1) if match else sender or "").strip().lower()


def _format_sender(sender: str, fallback_name: str) -> str:
    sender = (sender or "").strip()
    if not sender:
        sender = EMAIL_FROM_EMAIL
    if "<" in sender and ">" in sender:
        return sender
    return f"{fallback_name} <{sender}>"


def email_from(sender_label: str = "Security") -> str:
    label = (sender_label or "Security").strip().lower()
    if label in {"security", "login", "signup", "password", "password reset"}:
        return _format_sender(SECURITY_FROM_EMAIL, f"{APP_NAME} Security")
    if label in {"order", "orders", "receipt", "payment"}:
        return _format_sender(ORDERS_FROM_EMAIL, f"{APP_NAME} Orders")
    if label in {"delivery", "download", "downloads", "key", "keys"}:
        return _format_sender(DOWNLOADS_FROM_EMAIL, f"{APP_NAME} Downloads")
    if label in {"support", "ticket"}:
        return _format_sender(SUPPORT_FROM_EMAIL, f"{APP_NAME} Support")
    return _format_sender(NOTIFICATIONS_FROM_EMAIL or EMAIL_FROM_EMAIL, EMAIL_FROM_NAME or APP_NAME)


def sender_is_resend_safe(sender: str) -> bool:
    address = _extract_email_address(sender)
    if not address or "@" not in address:
        return False
    # Resend rejects public mailbox domains like gmail.com in the From field.
    # This app is configured for the verified moealturej.com domain.
    return address.endswith("@moealturej.com")


def send_email_message(to_email: str, subject: str, text: str, html_body: str, sender_label: str = "Security") -> bool:
    """Send transactional email through Resend using verified moealturej.com senders."""
    to_email = (to_email or "").strip()
    if not to_email or "@" not in to_email:
        logger.warning("Invalid email target for %s email: %s", sender_label, to_email)
        return False

    sender = email_from(sender_label)

    if EMAIL_PROVIDER == "smtp":
        if not SMTP_EMAIL or not SMTP_PASSWORD:
            logger.warning("SMTP is selected but SMTP_EMAIL/SMTP_PASSWORD are missing; could not send %s email to %s", sender_label, to_email)
            return False
        try:
            msg = EmailMessage()
            msg["From"] = sender
            msg["To"] = to_email
            msg["Subject"] = subject
            if RESEND_REPLY_TO:
                msg["Reply-To"] = RESEND_REPLY_TO
            msg.set_content(text)
            msg.add_alternative(html_body, subtype="html")
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=context, timeout=15) as server:
                server.login(SMTP_EMAIL, SMTP_PASSWORD)
                server.send_message(msg)
            return True
        except Exception:
            logger.exception("SMTP email failed for %s email to %s", sender_label, to_email)
            return False

    if not RESEND_API_KEY:
        logger.warning("RESEND_API_KEY is missing; could not send %s email to %s", sender_label, to_email)
        return False

    if not sender_is_resend_safe(sender):
        logger.error("Invalid Resend sender for %s email: %s. Use a verified @moealturej.com address, not Gmail.", sender_label, sender)
        return False

    payload = {
        "from": sender,
        "to": [to_email],
        "subject": subject,
        "text": text,
        "html": html_body,
    }
    if RESEND_REPLY_TO:
        payload["reply_to"] = RESEND_REPLY_TO

    try:
        resp = requests.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {RESEND_API_KEY}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=20,
        )
        if resp.status_code in (200, 201, 202):
            logger.info("Sent %s email to %s through Resend from %s", sender_label, to_email, sender)
            return True
        logger.error("Resend rejected %s email to %s: %s %s", sender_label, to_email, resp.status_code, resp.text[:500])
        return False
    except Exception:
        logger.exception("Resend request failed for %s email to %s", sender_label, to_email)
        return False



def brand_logo_html(size: int = 46) -> str:
    """Return the same brand mark used by security/OTP emails.

    Email clients need an absolute image URL. Prefer BRAND_LOGO_URL from .env,
    otherwise fall back to the public /static/logo.png on APP_URL.
    """
    safe_app = html_escape.escape(APP_NAME)
    logo_url = (BRAND_LOGO_URL or f"{APP_URL}/static/logo.png").strip()
    safe_logo = html_escape.escape(logo_url, quote=True) if logo_url else ""
    if safe_logo:
        return (
            f'<img src="{safe_logo}" alt="{safe_app}" width="{size}" height="{size}" '
            f'style="display:block;width:{size}px;height:{size}px;border-radius:14px;object-fit:cover;box-shadow:0 12px 30px rgba(172,89,255,.35);">'
        )
    initial = html_escape.escape((APP_NAME[:1] or "m").lower())
    return (
        f'<div style="width:{size}px;height:{size}px;border-radius:14px;background:linear-gradient(135deg,#8b5cf6,#ec4899);'
        f'display:grid;place-items:center;color:#fff;font-weight:900;font-size:20px;box-shadow:0 12px 30px rgba(172,89,255,.35);">{initial}</div>'
    )


def branded_email_shell(title: str, eyebrow: str, intro: str, body_html: str, cta_label: str = "Open account", cta_url: str | None = None, footer: str | None = None) -> str:
    """Clean, email-client-safe layout shared by all transactional messages."""
    safe_app = html_escape.escape(APP_NAME)
    safe_title = html_escape.escape(str(title or APP_NAME))
    safe_eyebrow = html_escape.escape(str(eyebrow or "Account update"))
    safe_intro = html_escape.escape(str(intro or ""))
    safe_cta_label = html_escape.escape(str(cta_label or "Open account"))
    safe_cta_url = html_escape.escape(str(cta_url or APP_URL), quote=True)
    support = html_escape.escape(SUPPORT_EMAIL)
    safe_footer = footer if footer is not None else f"Questions? Email <a href='mailto:{support}' style='color:#c4a7ff;text-decoration:none;'>{support}</a>."
    logo = brand_logo_html(42)
    return f"""<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="color-scheme" content="dark"><title>{safe_title}</title></head>
<body style="margin:0;padding:0;background:#07050b;color:#f8f5fb;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif;">
  <div style="display:none;max-height:0;overflow:hidden;opacity:0;color:transparent;">{safe_intro}</div>
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="width:100%;background:#07050b;">
    <tr><td align="center" style="padding:36px 16px;">
      <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="width:100%;max-width:600px;background:#100b18;border:1px solid #2a2038;border-radius:20px;overflow:hidden;">
        <tr><td style="height:3px;background:#8b3dea;font-size:0;line-height:0;">&nbsp;</td></tr>
        <tr><td style="padding:22px 24px;border-bottom:1px solid #2a2038;">
          <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0"><tr>
            <td width="54" valign="middle">{logo}</td>
            <td valign="middle"><div style="font-size:17px;font-weight:800;color:#ffffff;line-height:1.25;">{safe_app}</div><div style="margin-top:3px;font-size:12px;color:#93879f;line-height:1.4;">{safe_eyebrow}</div></td>
          </tr></table>
        </td></tr>
        <tr><td style="padding:30px 24px 10px;">
          <div style="font-size:12px;line-height:1.4;font-weight:800;letter-spacing:.08em;text-transform:uppercase;color:#b99ae9;">{safe_eyebrow}</div>
          <h1 style="margin:10px 0 9px;font-size:28px;line-height:1.18;letter-spacing:-.035em;color:#ffffff;">{safe_title}</h1>
          <p style="margin:0;color:#aaa0b3;font-size:15px;line-height:1.65;">{safe_intro}</p>
        </td></tr>
        <tr><td style="padding:18px 24px 22px;">{body_html}</td></tr>
        <tr><td style="padding:0 24px 30px;">
          <a href="{safe_cta_url}" style="display:inline-block;padding:13px 18px;border-radius:11px;background:#8b3dea;color:#ffffff;text-decoration:none;font-size:14px;font-weight:800;">{safe_cta_label}</a>
          <p style="margin:20px 0 0;color:#7f748a;font-size:12px;line-height:1.65;">{safe_footer}</p>
        </td></tr>
        <tr><td style="padding:16px 24px;background:#0b0811;border-top:1px solid #211a2b;color:#6f6677;font-size:11px;line-height:1.6;">Sent by {safe_app}. Never share verification codes or product keys with anyone you do not trust.</td></tr>
      </table>
    </td></tr>
  </table>
</body></html>"""

def build_security_email(code: str, purpose: str) -> tuple[str, str, str]:
    safe_code = html_escape.escape(str(code))
    label = str(purpose or "security").strip().title()
    safe_label = html_escape.escape(label)
    subject = f"{APP_NAME} security code: {code}"
    text = (
        f"Your {APP_NAME} {purpose} code is: {code}\n\n"
        "This code expires in 10 minutes.\n"
        f"Open {APP_URL} if you requested this.\n\n"
        f"If this was not you, ignore this email and contact {SUPPORT_EMAIL}."
    )
    body_html = f"""
      <div style="padding:20px;border-radius:14px;background:#0b0811;border:1px solid #2d2240;text-align:center;">
        <div style="font-size:12px;font-weight:800;letter-spacing:.08em;text-transform:uppercase;color:#8f839a;">{safe_label} code</div>
        <div style="margin-top:11px;font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:36px;line-height:1;font-weight:900;letter-spacing:10px;color:#ffffff;">{safe_code}</div>
        <div style="margin-top:13px;font-size:12px;font-weight:700;color:#b99ae9;">Expires in 10 minutes</div>
      </div>
      <p style="margin:16px 0 0;color:#8f8499;font-size:13px;line-height:1.65;">Didn’t request this? Ignore this email. Your account cannot be accessed without the code.</p>
    """
    html = branded_email_shell(
        "Your security code",
        "Secure account verification",
        f"Use this code to continue your {APP_NAME} {label.lower()} request.",
        body_html,
        f"Open {APP_NAME}",
        APP_URL,
    )
    return subject, text, html


def send_security_email(to_email: str, code: str, purpose: str) -> bool:
    subject, text, html = build_security_email(code, purpose)
    if not email_delivery_ready():
        logger.error("Email delivery is not configured. Add RESEND_API_KEY in production. The app defaults to security@moealturej.com for Resend.")
        return False
    return send_email_message(to_email, subject, text, html, "Security")

def start_email_code_flow(kind: str, payload: dict, email: str, purpose: str) -> bool:
    code = f"{secrets.randbelow(1000000):06d}"
    session[f"pending_{kind}"] = {
        "payload": payload,
        "email": email,
        "code_hash": generate_password_hash(code),
        "expires_at": (utc_now_naive() + timedelta(minutes=10)).isoformat(),
        "attempts": 0,
    }
    return send_security_email(email, code, purpose)


def consume_email_code_flow(kind: str, code: str) -> dict | None:
    pending = session.get(f"pending_{kind}")
    if not pending:
        return None
    try:
        expires_at = datetime.fromisoformat(pending.get("expires_at", ""))
    except Exception:
        expires_at = utc_now_naive() - timedelta(seconds=1)
    pending["attempts"] = int(pending.get("attempts", 0)) + 1
    session[f"pending_{kind}"] = pending
    if pending["attempts"] > 5 or utc_now_naive() > expires_at:
        session.pop(f"pending_{kind}", None)
        return None
    if not check_password_hash(pending.get("code_hash", ""), str(code or "").strip()):
        return None
    payload = pending.get("payload") or {}
    session.pop(f"pending_{kind}", None)
    return payload


def finish_login(user: dict):
    safe_user = sanitize_user_for_session(user)
    if not safe_user:
        abort(403, description="Invalid login session.")
    session["user"] = safe_user
    session.pop("csrf_token", None)



def google_oauth_ready() -> bool:
    return bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET)


def google_oauth_missing() -> list[str]:
    missing = []
    if not GOOGLE_CLIENT_ID:
        missing.append("GOOGLE_CLIENT_ID")
    if not GOOGLE_CLIENT_SECRET:
        missing.append("GOOGLE_CLIENT_SECRET")
    return missing


def google_redirect_uri() -> str:
    return GOOGLE_REDIRECT_URI or url_for("google_callback", _external=True)


def upsert_google_user(profile: dict) -> dict | None:
    google_id = str(profile.get("sub") or "").strip()
    email = str(profile.get("email") or "").strip().lower()
    username = str(profile.get("name") or (email.split("@")[0] if email else "google_user")).strip()
    avatar = str(profile.get("picture") or "").strip()
    if not google_id or not email or not using_mongo():
        if email and email == OWNER_EMAIL:
            return {"email": OWNER_EMAIL, "username": OWNER_USERNAME, "is_owner": True, "role": "owner", "auth_provider": "google", "google_id": google_id, "google_avatar": avatar}
        return None
    existing = users_col.find_one({"$or": [{"google_id": google_id}, {"email": email}]})
    role = "owner" if email == OWNER_EMAIL else ((existing or {}).get("role") or "user")
    payload = {
        "google_id": google_id,
        "google_avatar": avatar,
        "auth_provider": "google",
        "email": email,
        "email_verified": bool(profile.get("email_verified", True)),
        "username": username,
        "role": role,
        "is_owner": email == OWNER_EMAIL or role == "owner",
        "status": (existing or {}).get("status", "active"),
        "updated_at": utc_now_naive(),
    }
    if existing:
        users_col.update_one({"_id": existing["_id"]}, {"$set": payload})
    else:
        payload["created_at"] = utc_now_naive()
        payload["password_hash"] = ""
        users_col.insert_one(payload)
    return users_col.find_one({"google_id": google_id}) or users_col.find_one({"email": email})


def find_user_by_reset_token(token: str):
    if not token or not using_mongo():
        return None
    now = utc_now_naive()
    try:
        candidates = list(users_col.find({"password_reset_expires": {"$gt": now}}).limit(50))
    except Exception:
        candidates = []
    for user in candidates:
        reset_hash = user.get("password_reset_hash", "")
        if reset_hash and check_password_hash(reset_hash, token):
            return user
    return None


def send_password_reset_email(to_email: str, reset_url: str) -> bool:
    if not email_delivery_ready():
        logger.warning("Email delivery is not configured; cannot send password reset email.")
        return False
    safe_app = html_escape.escape(APP_NAME)
    safe_url = html_escape.escape(reset_url)
    safe_support = html_escape.escape(SUPPORT_EMAIL)
    subject = f"Reset your {APP_NAME} password"
    text = (
        f"Reset your {APP_NAME} password using this secure link: {reset_url}\n\n"
        "This link expires in 20 minutes. If you did not request it, ignore this email."
    )
    body_html = f"""<div style='border-radius:22px;background:rgba(255,255,255,.045);border:1px solid rgba(255,255,255,.10);padding:20px;text-align:center;'><p style='margin:0;color:#d9c5ff;line-height:1.65;'>Click the button below to create a new password. This secure link expires in 20 minutes.</p></div>"""
    html = branded_email_shell("Reset your password", "Password recovery", "Use this secure link to reset your password for your moealturej account.", body_html, "Reset password", reset_url)

    return send_email_message(to_email, subject, text, html, "Security")


def get_user_by_email(email: str | None) -> dict | None:
    email = (email or "").strip().lower()
    if not email or not using_mongo() or users_col is None:
        return None
    user = users_col.find_one({"email": email}, {"_id": 0})
    return user


def send_html_email(to_email: str, subject: str, text: str, html_body: str, sender_label: str = "Notifications") -> bool:
    return send_email_message(to_email, subject, text, html_body, sender_label)


def build_admin_broadcast_email(subject: str, message: str, cta_label: str = "Open moealturej", cta_url: str | None = None) -> tuple[str, str, str]:
    clean_subject = str(subject or "moealturej update").strip()[:120] or "moealturej update"
    clean_message = str(message or "").strip()
    safe_lines = "".join(f"<p style='margin:0 0 14px;color:#d9c5ff;line-height:1.7;'>{html_escape.escape(line)}</p>" for line in clean_message.splitlines() if line.strip())
    if not safe_lines:
        safe_lines = "<p style='margin:0;color:#d9c5ff;line-height:1.7;'>There is a new account update from moealturej.</p>"
    body_html = f"<div style='border-radius:22px;background:rgba(255,255,255,.045);border:1px solid rgba(255,255,255,.10);padding:20px;'>{safe_lines}</div>"
    text = f"{clean_subject}\n\n{clean_message}\n\n{APP_URL}"
    html = branded_email_shell(clean_subject, "Account update", "A new update from moealturej.", body_html, cta_label or "Open moealturej", cta_url or APP_URL)
    return clean_subject, text, html


def parse_admin_email_recipients() -> list[str]:
    """Collect valid recipients for the admin email sender without allowing bad form/user data to crash the request."""
    mode = (request.form.get("recipient_mode") or "manual").strip().lower()
    manual = request.form.get("manual_recipients") or ""
    selected = request.form.getlist("selected_recipients")
    recipients: set[str] = set()

    def add_email(value):
        email = str(value or "").strip().lower()
        if email and "@" in email and len(email) <= 254:
            recipients.add(email)

    if mode == "all":
        try:
            admin_users = load_admin_users()
        except Exception:
            logger.exception("Could not load users for admin bulk email")
            admin_users = []
        for user in admin_users:
            if not isinstance(user, dict):
                continue
            if user.get("status") != "suspended":
                add_email(user.get("email"))
    elif mode == "selected":
        for email in selected:
            add_email(email)
    else:
        for email in re.split(r"[\s,;]+", manual):
            add_email(email)
    return sorted(recipients)


def build_key_delivery_email(to_email: str, order: dict, item: dict, product_key: str, note: str = "") -> tuple[str, str, str]:
    safe_app = html_escape.escape(APP_NAME)
    safe_support = html_escape.escape(SUPPORT_EMAIL)
    safe_order = html_escape.escape(str(order.get("order_id", "")))
    safe_product = html_escape.escape(str(item.get("product_name", "Product")))
    safe_option = html_escape.escape(str(item.get("option_name", "")))
    safe_key = html_escape.escape(str(product_key))
    safe_note = html_escape.escape(str(note or ""))
    safe_url = APP_URL + "/account"
    subject = f"Your {safe_product} key is ready"
    text = (
        f"Your {APP_NAME} product key is ready.\n\n"
        f"Product: {item.get('product_name', 'Product')} — {item.get('option_name', '')}\n"
        f"Order: {order.get('order_id', '')}\n"
        f"Key: {product_key}\n\n"
        f"View it in your account: {APP_URL}/account\n"
        f"Need help? Contact {SUPPORT_EMAIL}."
    )
    body_html = f"""<div style='border-radius:22px;background:rgba(255,255,255,.045);border:1px solid rgba(255,255,255,.10);padding:20px;'><div style='font-size:13px;color:#a999c3;font-weight:800;text-transform:uppercase;letter-spacing:.08em;'>Product</div><div style='font-size:19px;font-weight:900;margin-top:6px;color:#fff;'>{safe_product} <span style='color:#b9a8d8;font-size:14px;'>— {safe_option}</span></div><div style='font-size:13px;color:#a999c3;margin-top:8px;'>Order {safe_order}</div><div style='margin-top:18px;padding:16px;border-radius:16px;background:#08040f;border:1px dashed rgba(216,180,254,.35);font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:18px;font-weight:900;letter-spacing:.04em;word-break:break-all;color:#fff;'>{safe_key}</div>{('<p style="margin:14px 0 0;color:#d9c5ff;line-height:1.6;">' + safe_note + '</p>') if safe_note else ''}</div>"""
    html = branded_email_shell("Your product key is ready", "Digital product delivery", "Thanks for your purchase. Your key is saved securely inside your account order history.", body_html, "Open account", safe_url)

    return subject, text, html


def discord_api_request(method: str, path: str, **kwargs):
    if not DISCORD_BOT_TOKEN:
        return None
    headers = kwargs.pop("headers", {}) or {}
    headers.setdefault("Authorization", f"Bot {DISCORD_BOT_TOKEN}")
    headers.setdefault("Content-Type", "application/json")
    try:
        resp = requests.request(method, f"{DISCORD_API_BASE}{path}", headers=headers, timeout=15, **kwargs)
        if resp.status_code == 429:
            retry_after = 2
            try:
                retry_after = min(8, float(resp.json().get("retry_after", 2)))
            except Exception:
                pass
            logger.warning("Discord rate limited request to %s; retry_after=%s", path, retry_after)
        return resp
    except Exception:
        logger.exception("Discord API request failed: %s %s", method, path)
        return None


def send_discord_key_dm(discord_id: str | None, order: dict, item: dict, product_key: str, note: str = "") -> bool:
    if not DELIVERY_DM_ENABLED or not DISCORD_BOT_TOKEN or not discord_id:
        return False
    channel_resp = discord_api_request("POST", "/users/@me/channels", json={"recipient_id": str(discord_id)})
    if not channel_resp or channel_resp.status_code >= 300:
        logger.warning("Could not create Discord DM channel for %s: %s", discord_id, getattr(channel_resp, 'text', '')[:180])
        return False
    channel_id = (channel_resp.json() or {}).get("id")
    if not channel_id:
        return False
    product_name = str(item.get("product_name", "Product"))
    option_name = str(item.get("option_name", ""))
    embed = {
        "title": "Your product key is ready",
        "description": f"Thanks for your purchase from {APP_NAME}. Your key is also saved in your website account.",
        "color": 0x9B5CFF,
        "fields": [
            {"name": "Product", "value": f"{product_name} — {option_name}"[:1024], "inline": False},
            {"name": "Order", "value": str(order.get("order_id", ""))[:1024], "inline": True},
            {"name": "Key", "value": f"```{str(product_key)[:900]}```", "inline": False},
        ],
        "footer": {"text": f"{APP_NAME} delivery"},
        "timestamp": utc_now().isoformat(),
    }
    if note:
        embed["fields"].append({"name": "Note", "value": str(note)[:1024], "inline": False})
    send_resp = discord_api_request("POST", f"/channels/{channel_id}/messages", json={"embeds": [embed]})
    if send_resp and send_resp.status_code < 300:
        return True
    logger.warning("Could not send Discord key DM to %s: %s", discord_id, getattr(send_resp, 'text', '')[:180])
    return False



class AutoDeliveryError(Exception):
    pass


NO_KEY_FALLBACK_MESSAGES = {
    "contact support to receive your key, reference code: bal_er",
    "contact support to receive your key, reference code: no_stk",
}


def is_no_key_fallback(product_key: str | None) -> bool:
    """Identify provider fallback messages without changing the delivered value."""
    normalized = " ".join(str(product_key or "").strip().lower().split())
    return normalized in NO_KEY_FALLBACK_MESSAGES


def normalize_auto_delivery_config(config: dict | None) -> dict:
    """Return a safe, non-secret auto-delivery config for storing on order items."""
    if not isinstance(config, dict):
        return {"enabled": False}
    provider = str(config.get("provider") or "reselling_pro").strip().lower()
    base_url = str(config.get("base_url") or config.get("baseUrl") or config.get("url") or "").strip()
    enabled = bool(config.get("enabled")) and provider == "reselling_pro" and bool(base_url)
    return {"enabled": enabled, "provider": "reselling_pro", "base_url": base_url}


def item_auto_delivery_config(product: dict, option: dict) -> dict:
    """Option-level config wins; product-level config is the fallback."""
    product_store = product.get("store") or {}
    option_config = normalize_auto_delivery_config(option.get("autoDelivery") or option.get("auto_delivery"))
    if option_config.get("enabled"):
        return option_config
    return normalize_auto_delivery_config(product_store.get("autoDelivery") or product_store.get("auto_delivery"))


def build_reselling_pro_delivery_url(base_url: str) -> str:
    if not RESELLING_PRO_ENABLED:
        raise AutoDeliveryError("Reselling.pro auto-delivery is disabled.")
    if not RESELLING_PRO_API_KEY:
        raise AutoDeliveryError("RESELLING_PRO_API_KEY is missing from .env.")
    base_url = str(base_url or "").strip().rstrip("/")
    if not base_url:
        raise AutoDeliveryError("Auto-delivery base URL is missing.")
    parsed = urlparse(base_url)
    if parsed.scheme != "https" or parsed.netloc.lower() != RESELLING_PRO_ALLOWED_HOST:
        raise AutoDeliveryError("Auto-delivery URL must be an HTTPS api.reselling.pro URL.")
    if not parsed.path.startswith("/rft/api/seller/keys/"):
        raise AutoDeliveryError("Auto-delivery URL must use the Reselling.pro seller keys endpoint.")
    return f"{base_url}/{RESELLING_PRO_API_KEY}"


def extract_product_key_from_response(resp: requests.Response) -> str:
    text = (resp.text or "").strip()
    try:
        data = resp.json()
    except Exception:
        data = None
    if isinstance(data, dict):
        for key_name in ("key", "license", "license_key", "code", "data", "result"):
            value = data.get(key_name)
            if isinstance(value, str) and value.strip():
                return value.strip()
        nested = data.get("data")
        if isinstance(nested, dict):
            for key_name in ("key", "license", "license_key", "code"):
                value = nested.get(key_name)
                if isinstance(value, str) and value.strip():
                    return value.strip()
    if isinstance(data, str) and data.strip():
        return data.strip()
    if text:
        return text
    raise AutoDeliveryError("Provider returned an empty key response.")


def fetch_reselling_pro_key(base_url: str) -> str:
    """Fetch exactly one product key. Never logs or exposes the secret API URL."""
    delivery_url = build_reselling_pro_delivery_url(base_url)
    try:
        resp = requests.get(delivery_url, timeout=RESELLING_PRO_TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        raise AutoDeliveryError(f"Could not contact Reselling.pro: {exc.__class__.__name__}") from exc
    if resp.status_code >= 400:
        safe_body = (resp.text or "").strip().replace(RESELLING_PRO_API_KEY, "[hidden]")[:220]
        raise AutoDeliveryError(f"Reselling.pro returned HTTP {resp.status_code}: {safe_body}")
    product_key = extract_product_key_from_response(resp)
    if not product_key or len(product_key.strip()) < 3:
        raise AutoDeliveryError("Provider returned an invalid key.")
    return product_key.strip()


def save_delivery_to_order(order: dict, item: dict, item_index: int, product_key: str, note: str = "", source: str = "manual") -> dict:
    deliveries = order.get("deliveries") or {}
    delivery_id = str(item_index)
    deliveries[delivery_id] = {
        "item_index": item_index,
        "product_name": item.get("product_name"),
        "option_name": item.get("option_name"),
        "product_key": product_key,
        "note": note,
        "sent_at": utc_now().isoformat(),
        "sent_by": source,
        "source": source,
        "email_sent": False,
        "discord_dm_sent": False,
    }
    order["deliveries"] = deliveries
    return deliveries[delivery_id]


def notify_buyer_delivery(order: dict, item: dict, delivery: dict) -> dict:
    buyer = get_user_by_email(order.get("user_email")) or {}
    product_key = delivery.get("product_key", "")
    note = delivery.get("note", "")
    email_sent = False
    dm_sent = False
    if order.get("user_email"):
        subject, text, html_body = build_key_delivery_email(order.get("user_email"), order, item, product_key, note)
        email_sent = send_html_email(order.get("user_email"), subject, text, html_body, "Delivery")
    if buyer.get("discord_id"):
        dm_sent = send_discord_key_dm(buyer.get("discord_id"), order, item, product_key, note)
    delivery["email_sent"] = email_sent
    delivery["discord_dm_sent"] = dm_sent
    return delivery


def process_auto_delivery(order: dict) -> dict:
    """Run auto-delivery once when an order first becomes paid."""
    if str(order.get("status", "")).lower() != "paid":
        return order
    # Payment providers may repeat paid webhooks. This persisted marker prevents
    # duplicate provider requests while keeping the original result intact.
    if order.get("auto_delivery_processed_at"):
        return order

    processed_at = utc_now().isoformat()
    order["auto_delivery_processed_at"] = processed_at
    cart_items = (order.get("cart") or {}).get("items") or []
    deliveries = order.get("deliveries") or {}
    attempts = order.get("auto_delivery_attempts") or {}
    failures = []
    no_key_items = []
    delivered_count = 0

    for index, item in enumerate(cart_items):
        delivery_id = str(index)
        if deliveries.get(delivery_id):
            continue
        config = item.get("auto_delivery") or {}
        if not config.get("enabled"):
            continue
        quantity = min(max(int(item.get("quantity") or 1), 1), 10)
        base_url = str(config.get("base_url") or "").strip()
        keys = []
        try:
            for _ in range(quantity):
                keys.append(fetch_reselling_pro_key(base_url))

            fallback_codes = [key for key in keys if is_no_key_fallback(key)]
            if fallback_codes:
                no_key_items.append({
                    "item_index": index,
                    "product_name": item.get("product_name"),
                    "option_name": item.get("option_name"),
                })

            note = "Auto-delivered by moealturej. Keep this key private."
            delivery = save_delivery_to_order(order, item, index, "\n".join(keys), note, "reselling_pro_auto")
            notify_buyer_delivery(order, item, delivery)
            delivered_count += 1
            attempts[delivery_id] = {
                "status": "delivered",
                "at": processed_at,
                "quantity": quantity,
                "no_key_fallback": bool(fallback_codes),
            }
        except Exception as exc:
            message = str(exc)[:500]
            failures.append({"item_index": index, "product_name": item.get("product_name"), "option_name": item.get("option_name"), "error": message, "at": processed_at})
            attempts[delivery_id] = {"status": "failed", "at": processed_at, "error": message}
            logger.warning("Auto-delivery failed for order %s item %s: %s", order.get("order_id"), index, message)

    order["auto_delivery_attempts"] = attempts
    if no_key_items:
        order["auto_delivery_no_key_items"] = no_key_items
        order["auto_delivery_no_key"] = True
    else:
        order.pop("auto_delivery_no_key_items", None)
        order.pop("auto_delivery_no_key", None)

    if failures:
        order["auto_delivery_failures"] = failures
    elif order.get("auto_delivery_failures"):
        order.pop("auto_delivery_failures", None)
    if cart_items and len((order.get("deliveries") or {})) >= len(cart_items):
        order["delivery_status"] = "delivered"
    elif delivered_count:
        order["delivery_status"] = "partial"
    elif any((item.get("auto_delivery") or {}).get("enabled") for item in cart_items):
        order["delivery_status"] = "auto_failed" if failures else order.get("delivery_status", "pending")
    else:
        order.setdefault("delivery_status", "manual_required")
    order["updated_at"] = processed_at
    return order

def format_order_items_for_discord(cart: dict, deliveries: dict | None = None) -> str:
    lines = []
    deliveries = deliveries or {}
    for index, item in enumerate((cart or {}).get("items", [])[:10]):
        delivered = " ✅ delivered" if deliveries.get(str(index)) else " ⏳ needs key"
        auto = " · auto" if (item.get("auto_delivery") or {}).get("enabled") else " · manual"
        lines.append(f"• {item.get('product_name','Product')} — {item.get('option_name','Option')} × {item.get('quantity',1)} — ${item.get('line_amount','0.00')}{auto}{delivered}")
    return "\n".join(lines) or "No items found."


def send_owner_order_webhook(order: dict) -> bool:
    if not OWNER_ORDER_WEBHOOK_URL or not str(order.get("status", "")).lower() == "paid":
        return False
    cart = order.get("cart") or {}
    deliveries = order.get("deliveries") or {}
    failures = order.get("auto_delivery_failures") or []
    buyer = get_user_by_email(order.get("user_email")) or {}
    undelivered = []
    for index, item in enumerate((cart or {}).get("items", [])):
        if not deliveries.get(str(index)):
            undelivered.append(item)
    fields = [
        {"name": "Buyer", "value": str(order.get("user_email") or "Unknown")[:1024], "inline": True},
        {"name": "Provider", "value": str(order.get("provider") or "payment").title()[:1024], "inline": True},
        {"name": "Total", "value": f"{order.get('currency','USD')} ${cents_to_money(order.get('amount_cents', 0))}", "inline": True},
        {"name": "Order ID", "value": f"`{order.get('order_id','')}`"[:1024], "inline": False},
        {"name": "Delivery status", "value": str(order.get("delivery_status") or "pending")[:1024], "inline": True},
        {"name": "Items", "value": format_order_items_for_discord(cart, deliveries)[:1024], "inline": False},
    ]
    if failures:
        fields.append({"name": "Auto-delivery failure", "value": "\n".join([f"• {f.get('product_name','Product')} — {f.get('option_name','Option')}: {f.get('error','failed')}" for f in failures[:5]])[:1024], "inline": False})
    if buyer.get("discord_id"):
        fields.append({"name": "Discord linked", "value": f"Yes — `{buyer.get('discord_id')}`", "inline": True})
    # Keep this as the final webhook field so the owner sees the alert at the bottom.
    if order.get("auto_delivery_no_key"):
        fields.append({"name": "\u200b", "value": "**NO KEY**", "inline": False})
    fully_delivered = bool(cart.get("items")) and not undelivered
    content = "✅ Paid order auto-delivered." if fully_delivered else "✅ New paid order needs key delivery / review."
    description = (
        f"All configured keys were delivered for order `{order.get('order_id','')}`."
        if fully_delivered
        else f"Open the owner dashboard and deliver/review remaining keys for order `{order.get('order_id','')}`."
    )
    payload = {
        "username": f"{APP_NAME} Orders",
        "content": content,
        "embeds": [{
            "title": "New paid order",
            "description": description,
            "color": 0x39E58C if fully_delivered else 0x9B5CFF,
            "fields": fields,
            "footer": {"text": f"{APP_NAME} owner notification"},
            "timestamp": utc_now().isoformat(),
        }],
    }
    try:
        resp = requests.post(OWNER_ORDER_WEBHOOK_URL, json=payload, timeout=12)
        if resp.status_code in {200, 204}:
            return True
        logger.warning("Owner order webhook failed: %s %s", resp.status_code, resp.text[:180])
    except Exception:
        logger.exception("Owner order webhook failed")
    return False

def discord_oauth_ready() -> bool:
    return bool(DISCORD_CLIENT_ID and DISCORD_CLIENT_SECRET)

def discord_oauth_missing() -> list[str]:
    missing = []
    if not DISCORD_CLIENT_ID:
        missing.append("DISCORD_CLIENT_ID")
    if not DISCORD_CLIENT_SECRET:
        missing.append("DISCORD_CLIENT_SECRET")
    return missing


def discord_guild_join_ready() -> bool:
    return bool(DISCORD_AUTO_JOIN_GUILD and DISCORD_BOT_TOKEN and DISCORD_GUILD_ID)


def discord_oauth_scopes() -> str:
    scopes = ["identify", "email"]
    if discord_guild_join_ready():
        scopes.append("guilds.join")
    return " ".join(scopes)


def add_discord_user_to_guild(discord_id: str, user_access_token: str) -> tuple[bool, str]:
    """Add the OAuth user to the configured Discord guild using the bot token.

    Requires the OAuth scope `guilds.join`, a valid bot token, and the bot already
    being present in the server. Returns (success, message).
    """
    if not DISCORD_AUTO_JOIN_GUILD:
        return True, "Auto join disabled."
    if not DISCORD_BOT_TOKEN:
        return False, "DISCORD_BOT_TOKEN is missing."
    if not DISCORD_GUILD_ID:
        return False, "DISCORD_GUILD_ID is missing."
    if not discord_id or not user_access_token:
        return False, "Missing Discord user/access token."
    try:
        resp = requests.put(
            f"{DISCORD_API_BASE}/guilds/{DISCORD_GUILD_ID}/members/{discord_id}",
            headers={
                "Authorization": f"Bot {DISCORD_BOT_TOKEN}",
                "Content-Type": "application/json",
            },
            json={"access_token": user_access_token},
            timeout=12,
        )
        if resp.status_code in {201, 204}:
            return True, "Joined Discord server."
        if resp.status_code == 400:
            return False, "Discord rejected the join. Make sure OAuth includes guilds.join and the user approved it."
        if resp.status_code == 401:
            return False, "Discord bot token is invalid."
        if resp.status_code == 403:
            return False, "Bot cannot add members. Make sure the bot is in the server and has permission."
        if resp.status_code == 404:
            return False, "Discord server was not found. Check DISCORD_GUILD_ID."
        if resp.status_code == 429:
            return False, "Discord rate limited the server join. Try again shortly."
        return False, f"Discord join failed with {resp.status_code}: {resp.text[:180]}"
    except Exception as exc:
        logger.error("Discord guild join failed: %s", exc)
        return False, "Discord server join failed. Check bot token and server settings."


def discord_redirect_uri() -> str:
    return DISCORD_REDIRECT_URI or url_for("discord_callback", _external=True)


def discord_avatar_url(discord_id: str | None, avatar_hash: str | None) -> str | None:
    if not discord_id or not avatar_hash:
        return None
    ext = "gif" if str(avatar_hash).startswith("a_") else "png"
    return f"https://cdn.discordapp.com/avatars/{discord_id}/{avatar_hash}.{ext}?size=128"


def upsert_discord_user(profile: dict) -> dict | None:
    discord_id = str(profile.get("id") or "").strip()
    email = str(profile.get("email") or "").strip().lower()
    username = str(profile.get("global_name") or profile.get("username") or "discord_user").strip()
    avatar = discord_avatar_url(discord_id, profile.get("avatar"))
    if not discord_id:
        return None
    if not using_mongo():
        if email and email == OWNER_EMAIL:
            return {"email": OWNER_EMAIL, "username": OWNER_USERNAME, "is_owner": True, "role": "owner", "auth_provider": "discord", "discord_id": discord_id, "discord_avatar": avatar}
        return None
    existing = users_col.find_one({"$or": [{"discord_id": discord_id}, {"email": email}]}) if email else users_col.find_one({"discord_id": discord_id})
    role = "owner" if email == OWNER_EMAIL else ((existing or {}).get("role") or "user")
    payload = {
        "discord_id": discord_id,
        "discord_username": profile.get("username"),
        "discord_global_name": profile.get("global_name"),
        "discord_avatar": avatar,
        "discord_verified": bool(profile.get("verified")),
        "auth_provider": "discord",
        "username": username,
        "role": role,
        "is_owner": email == OWNER_EMAIL or role == "owner",
        "status": (existing or {}).get("status", "active"),
        "updated_at": utc_now_naive(),
    }
    if email:
        payload["email"] = email
        payload["email_verified"] = bool(profile.get("verified"))
    if existing:
        users_col.update_one({"_id": existing["_id"]}, {"$set": payload})
        user = users_col.find_one({"_id": existing["_id"]})
    else:
        payload.setdefault("email", f"discord-{discord_id}@discord.local")
        payload["created_at"] = utc_now_naive()
        payload["password_hash"] = generate_password_hash(secrets.token_urlsafe(32))
        users_col.insert_one(payload)
        user = users_col.find_one({"discord_id": discord_id})
    return user


def load_media() -> list:
    if using_mongo() and media_col is not None:
        try:
            return list(media_col.find({}, {"_id": 0}).sort("created_at", -1))
        except Exception:
            logger.exception("Failed to read media from MongoDB")
    if MEDIA_FILE.exists():
        try:
            return json.loads(MEDIA_FILE.read_text(encoding="utf-8"))
        except Exception:
            logger.exception("Failed to read media fallback file")
    return []


def save_media_record(record: dict):
    record.setdefault("created_at", utc_now().isoformat())
    if using_mongo() and media_col is not None:
        media_col.update_one({"filename": record["filename"]}, {"$set": record}, upsert=True)
    else:
        DATA_DIR.mkdir(exist_ok=True)
        records = [r for r in load_media() if r.get("filename") != record.get("filename")]
        records.insert(0, record)
        MEDIA_FILE.write_text(json.dumps(records, indent=2), encoding="utf-8")


def delete_media_record(filename: str):
    if using_mongo() and media_col is not None:
        media_col.delete_one({"filename": filename})
    else:
        records = [r for r in load_media() if r.get("filename") != filename]
        MEDIA_FILE.write_text(json.dumps(records, indent=2), encoding="utf-8")


def save_upload_to_mongo_storage(local_path: Path, filename: str, original_name: str, upload_kind: str, mime_type: str) -> str | None:
    """Persist dashboard uploads in MongoDB GridFS so they survive Render restarts."""
    if not (using_mongo() and media_fs is not None):
        return None
    try:
        # Keep one active GridFS file per generated filename.
        existing = list(media_fs.find({"filename": filename}))
        for grid_file in existing:
            media_fs.delete(grid_file._id)
        with local_path.open("rb") as file_handle:
            file_id = media_fs.put(
                file_handle,
                filename=filename,
                content_type=mime_type or "application/octet-stream",
                metadata={
                    "original_name": original_name,
                    "kind": upload_kind,
                    "created_at": utc_now().isoformat(),
                },
            )
        return str(file_id)
    except Exception:
        logger.exception("Failed to persist uploaded file %s to MongoDB GridFS", filename)
        return None


def get_mongo_stored_file(filename: str):
    if not (using_mongo() and media_fs is not None):
        return None
    try:
        return media_fs.find_one({"filename": secure_filename(filename)})
    except Exception:
        logger.exception("Failed to read uploaded file %s from MongoDB GridFS", filename)
        return None


def delete_mongo_stored_file(filename: str):
    if not (using_mongo() and media_fs is not None):
        return
    try:
        for grid_file in list(media_fs.find({"filename": secure_filename(filename)})):
            media_fs.delete(grid_file._id)
    except Exception:
        logger.exception("Failed to delete uploaded file %s from MongoDB GridFS", filename)


def send_mongo_stored_file(filename: str, as_attachment: bool = False):
    grid_file = get_mongo_stored_file(filename)
    if grid_file is None:
        abort(404)

    mimetype = getattr(grid_file, "content_type", None) or mimetypes.guess_type(filename)[0] or "application/octet-stream"
    disposition = "attachment" if as_attachment else "inline"
    safe_name = secure_filename(filename)
    headers = {
        "Content-Disposition": f'{disposition}; filename="{safe_name}"',
        "Cache-Control": "public, max-age=31536000" if IS_PRODUCTION else "no-cache",
    }
    try:
        size = getattr(grid_file, "length", None)
        if size is not None:
            headers["Content-Length"] = str(size)
    except Exception:
        pass

    return Response(grid_file.read(), mimetype=mimetype, headers=headers)


def allowed_upload(filename: str, upload_kind: str) -> bool:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if upload_kind == "image":
        return ext in ALLOWED_IMAGE_EXTENSIONS
    return ext in ALLOWED_FILE_EXTENSIONS


def human_file_size(size_bytes: int | None) -> str:
    try:
        size = float(size_bytes or 0)
    except (TypeError, ValueError):
        size = 0
    units = ["B", "KB", "MB", "GB"]
    unit = 0
    while size >= 1024 and unit < len(units) - 1:
        size /= 1024
        unit += 1
    if unit == 0:
        return f"{int(size)} {units[unit]}"
    return f"{size:.1f} {units[unit]}"


def record_audit(action: str, target: str = "", details: dict | None = None):
    if not (using_mongo() and audit_col is not None):
        return
    user = current_user() or {}
    try:
        audit_col.insert_one({
            "action": action,
            "target": target,
            "details": details or {},
            "actor": user.get("email", "system"),
            "ip": request.headers.get("X-Forwarded-For", request.remote_addr),
            "created_at": utc_now_naive(),
        })
    except Exception:
        logger.exception("Failed to record audit log")


def load_admin_users() -> list:
    if not using_mongo():
        return [{"email": OWNER_EMAIL, "username": OWNER_USERNAME, "role": "owner", "is_owner": True, "status": "active"}]
    users = list(users_col.find({}, {"_id": 0, "password_hash": 0}).sort("created_at", -1))
    for u in users:
        u.setdefault("status", "active")
        u.setdefault("role", "owner" if u.get("is_owner") else "user")
    return users

def load_products() -> list:
    """Load product data from MongoDB when configured, otherwise JSON."""
    if using_mongo():
        try:
            products = [normalize_product(p) for p in products_col.find({}, {"_id": 0})]
            return sorted(products, key=lambda p: (int(p.get("displayOrder", 9999)), str(p.get("name", "")).lower()))
        except Exception:
            logger.exception("Failed to read products from MongoDB. Falling back to JSON.")

    if not PRODUCTS_FILE.exists():
        logger.warning("Products file does not exist: %s", PRODUCTS_FILE)
        return []

    try:
        with PRODUCTS_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            logger.warning("Products JSON is not a list.")
            return []
        products = [normalize_product(p) for p in data if isinstance(p, dict)]
        return sorted(products, key=lambda p: (int(p.get("displayOrder", 9999)), str(p.get("name", "")).lower()))
    except json.JSONDecodeError:
        logger.exception("Failed to decode products JSON.")
        return []
    except OSError:
        logger.exception("Failed to read products file.")
        return []


def is_enabled_section(product: dict, section_name: str) -> bool:
    """Check whether a product section is enabled."""
    if not isinstance(product, dict):
        return False

    section = product.get(section_name, {})
    return isinstance(section, dict) and section.get("enabled") is True


def is_store_product(product: dict) -> bool:
    """Paid/store products only."""
    return is_enabled_section(product, "store")


def is_download_product(product: dict) -> bool:
    """Products shown on downloads page, including free products."""
    return is_enabled_section(product, "downloads")


def is_status_product(product: dict) -> bool:
    """Products shown on status page, including free products."""
    return is_enabled_section(product, "status")


def filter_products(section_name: str) -> list:
    """Return products where a given section is enabled."""
    products = load_products()

    if section_name == "store":
        return [product for product in products if is_store_product(product)]

    if section_name == "downloads":
        return [product for product in products if is_download_product(product)]

    if section_name == "status":
        return [product for product in products if is_status_product(product)]

    return [
        product
        for product in products
        if isinstance(product, dict) and product.get(section_name, {}).get("enabled") is True
    ]


def find_store_product(identifier: str) -> dict | None:
    """Find a store product by slug or id from products.json."""
    identifier = str(identifier or "").strip().lower()

    if not identifier:
        return None

    for product in filter_products("store"):
        product_slug = str(product.get("slug", "")).strip().lower()
        product_id = str(product.get("id", "")).strip().lower()

        if identifier in {product_slug, product_id}:
            return product

    return None


def money_to_cents(value) -> int:
    try:
        return max(0, int(round(float(value) * 100)))
    except Exception:
        return 0


def cents_to_money(cents: int) -> str:
    return f"{max(0, int(cents)) / 100:.2f}"

def object_get(obj, key, default=None):
    """Safely read a value from dicts and SDK objects like StripeObject.

    Newer stripe-python objects do not expose dict.get(), so calling
    session_obj.get("id") can raise AttributeError('get') even after Stripe
    successfully creates the checkout session.
    """
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    try:
        return obj[key]
    except Exception:
        return getattr(obj, key, default)



def clean_discount_code(code: str) -> str:
    code = str(code or "").upper().strip()
    code = re.sub(r"[^A-Z0-9_-]", "", code)
    return code[:DISCOUNT_CODE_MAX_LEN]


def normalize_discount_type(value: str) -> str:
    return "fixed" if str(value or "").lower() == "fixed" else "percent"


def discount_value_cents(discount: dict, base_cents: int) -> int:
    if not isinstance(discount, dict) or base_cents <= 0:
        return 0
    dtype = normalize_discount_type(discount.get("type"))
    try:
        raw_value = float(discount.get("value") or 0)
    except Exception:
        raw_value = 0
    if raw_value <= 0:
        return 0
    if dtype == "fixed":
        return min(base_cents, money_to_cents(raw_value))
    return min(base_cents, int(round(base_cents * (min(raw_value, 95) / 100))))


def discount_min_subtotal_cents(discount: dict) -> int:
    try:
        return max(0, money_to_cents(discount.get("min_subtotal") or 0))
    except Exception:
        return 0


def discount_is_active(discount: dict, subtotal_cents: int, quantity: int = 0) -> bool:
    if not isinstance(discount, dict) or not discount.get("enabled"):
        return False
    if subtotal_cents < discount_min_subtotal_cents(discount):
        return False
    expires = str(discount.get("expires_at") or "").strip()
    if expires:
        try:
            if datetime.fromisoformat(expires.replace("Z", "+00:00")) < utc_now():
                return False
        except Exception:
            return False
    if discount.get("max_uses"):
        try:
            if int(discount.get("used_count") or 0) >= int(discount.get("max_uses") or 0):
                return False
        except Exception:
            return False
    if discount.get("min_quantity"):
        try:
            if int(quantity or 0) < int(discount.get("min_quantity") or 0):
                return False
        except Exception:
            return False
    return True


def get_discount_settings() -> dict:
    settings = load_site_settings().get("discounts")
    defaults = default_discount_settings()
    if isinstance(settings, dict):
        merged = defaults
        for key, value in settings.items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key].update(value)
            else:
                merged[key] = value
        return merged
    return defaults


def apply_checkout_discounts(line_items: list, subtotal_cents: int, total_quantity: int, discount_code: str = "", user: dict | None = None) -> tuple[int, list]:
    discounts = get_discount_settings()
    if not discounts.get("enabled", True):
        return 0, []
    applied = []
    discount_total = 0

    def add_discount(source: str, discount: dict, base_cents: int, code: str = ""):
        nonlocal discount_total
        amount = discount_value_cents(discount, max(0, base_cents))
        amount = min(amount, max(0, subtotal_cents - discount_total))
        if amount <= 0:
            return
        applied.append({
            "source": source,
            "code": code,
            "label": str(discount.get("label") or discount.get("name") or source.replace("_", " ").title())[:80],
            "type": normalize_discount_type(discount.get("type")),
            "value": float(discount.get("value") or 0),
            "amount_cents": amount,
            "amount": cents_to_money(amount),
        })
        discount_total += amount

    store_auto = discounts.get("store_auto") or {}
    if discount_is_active(store_auto, subtotal_cents, total_quantity):
        add_discount("store_auto", store_auto, subtotal_cents)

    bulk = discounts.get("bulk") or {}
    if discount_is_active(bulk, subtotal_cents, total_quantity):
        add_discount("bulk", bulk, subtotal_cents)

    if (user or {}).get("discord_id"):
        discord = discounts.get("discord") or {}
        if discount_is_active(discord, subtotal_cents, total_quantity):
            add_discount("discord", discord, subtotal_cents)

    requested_code = clean_discount_code(discount_code)
    if requested_code:
        for code_discount in discounts.get("codes") or []:
            if clean_discount_code(code_discount.get("code")) == requested_code and discount_is_active(code_discount, subtotal_cents, total_quantity):
                add_discount("code", code_discount, subtotal_cents, requested_code)
                break

    # Product auto-discounts are calculated per line after global discounts so a single item cannot over-discount the cart.
    for item in line_items:
        product_discount = item.get("product_auto_discount") or {}
        if discount_is_active(product_discount, item.get("line_cents", 0), item.get("quantity", 0)):
            add_discount("product_auto", product_discount, item.get("line_cents", 0), str(item.get("slug", "")))

    return discount_total, applied


def increment_discount_code_usage(cart: dict):
    applied_codes = {clean_discount_code(d.get("code")) for d in (cart or {}).get("discounts", []) if d.get("source") == "code" and d.get("code")}
    if not applied_codes:
        return
    settings = load_site_settings()
    discounts = settings.get("discounts") if isinstance(settings.get("discounts"), dict) else default_discount_settings()
    changed = False
    for code_discount in discounts.get("codes") or []:
        if clean_discount_code(code_discount.get("code")) in applied_codes:
            code_discount["used_count"] = int(code_discount.get("used_count") or 0) + 1
            changed = True
    if changed:
        settings["discounts"] = discounts
        save_site_settings(settings)



def build_checkout_cart(raw_items: list, discount_code: str = "", user: dict | None = None) -> dict:
    if not isinstance(raw_items, list) or not raw_items:
        raise ValueError("Your cart is empty.")
    line_items = []
    subtotal_cents = 0
    total_quantity = 0
    for raw in raw_items[:30]:
        slug = str(raw.get("slug") or raw.get("productSlug") or raw.get("product_id") or raw.get("productId") or "").strip()
        option_id = str(raw.get("option_id") or raw.get("optionId") or raw.get("uniqid") or raw.get("variantId") or "").strip()
        quantity = int(raw.get("quantity") or 1)
        quantity = min(max(quantity, 1), 10)
        product = find_store_product(slug)
        if not product:
            continue
        options = (product.get("store") or {}).get("options") or []
        option = None
        for opt in options:
            ids = {str(opt.get("id", "")), str(opt.get("uniqid", ""))}
            if option_id in ids:
                option = opt
                break
        if not option and options:
            option = options[0]
        if not option:
            continue
        unit_cents = money_to_cents(option.get("price", 0))
        if unit_cents <= 0:
            continue
        line_total = unit_cents * quantity
        subtotal_cents += line_total
        total_quantity += quantity
        line_items.append({
            "slug": product.get("slug"),
            "product_id": product.get("id"),
            "product_name": product.get("name", "Product"),
            "image": product.get("image", "/static/logo.png"),
            "option_id": str(option.get("id") or option.get("uniqid") or option_id),
            "option_name": option.get("name", "Option"),
            "unit_cents": unit_cents,
            "unit_amount": cents_to_money(unit_cents),
            "quantity": quantity,
            "line_cents": line_total,
            "line_amount": cents_to_money(line_total),
            "auto_delivery": item_auto_delivery_config(product, option),
            "product_auto_discount": (product.get("store") or {}).get("autoDiscount") or {},
        })
    if not line_items:
        raise ValueError("No valid products are in the cart.")
    discount_cents, applied_discounts = apply_checkout_discounts(line_items, subtotal_cents, total_quantity, discount_code, user)
    taxable_cents = max(0, subtotal_cents - discount_cents)
    fee_cents = int(round(taxable_cents * (PROCESSING_FEE_PERCENT / 100)))
    total_cents = taxable_cents + fee_cents
    for item in line_items:
        item.pop("product_auto_discount", None)
    return {
        "items": line_items,
        "subtotal_cents": subtotal_cents,
        "discount_cents": discount_cents,
        "fee_cents": fee_cents,
        "total_cents": total_cents,
        "subtotal": cents_to_money(subtotal_cents),
        "discount": cents_to_money(discount_cents),
        "fee": cents_to_money(fee_cents),
        "total": cents_to_money(total_cents),
        "currency": STRIPE_CURRENCY.upper(),
        "quantity": total_quantity,
        "discount_code": clean_discount_code(discount_code),
        "discounts": applied_discounts,
    }


def save_order(order: dict):
    order.setdefault("created_at", utc_now().isoformat())
    order.setdefault("updated_at", utc_now().isoformat())
    if using_mongo() and orders_col is not None:
        orders_col.update_one({"order_id": order["order_id"]}, {"$set": order}, upsert=True)
        return
    DATA_DIR.mkdir(exist_ok=True)
    orders = []
    if ORDERS_FILE.exists():
        try:
            orders = json.loads(ORDERS_FILE.read_text(encoding="utf-8"))
        except Exception:
            orders = []
    orders = [o for o in orders if o.get("order_id") != order.get("order_id")]
    orders.insert(0, order)
    ORDERS_FILE.write_text(json.dumps(orders[:1000], indent=2), encoding="utf-8")


def load_orders_for_user(email: str | None = None) -> list:
    email = (email or "").strip().lower()
    if using_mongo() and orders_col is not None:
        query = {"user_email": email} if email else {}
        return list(orders_col.find(query, {"_id": 0}).sort("created_at", -1).limit(50))
    if ORDERS_FILE.exists():
        try:
            orders = json.loads(ORDERS_FILE.read_text(encoding="utf-8"))
        except Exception:
            orders = []
        if email:
            orders = [o for o in orders if str(o.get("user_email", "")).lower() == email]
        return orders[:50]
    return []






def parse_order_datetime(value) -> datetime | None:
    """Return an aware UTC datetime from the mixed formats used in saved orders."""
    if isinstance(value, datetime):
        dt = value
    else:
        raw = str(value or "").strip()
        if not raw:
            return None
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except Exception:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def order_is_expired_pending(order: dict, now: datetime | None = None) -> bool:
    if str(order.get("status", "")).lower() != "pending":
        return False
    created_at = parse_order_datetime(order.get("created_at"))
    if not created_at:
        return False
    now = now or utc_now()
    return now - created_at >= timedelta(minutes=PENDING_ORDER_MAX_AGE_MINUTES)


def delete_order(order_id: str) -> bool:
    order_id = (order_id or "").strip()
    if not order_id:
        return False
    if using_mongo() and orders_col is not None:
        result = orders_col.delete_one({"order_id": order_id})
        return bool(getattr(result, "deleted_count", 0))
    orders = load_orders_for_user(None)
    kept = [o for o in orders if str(o.get("order_id", "")) != order_id]
    if len(kept) == len(orders):
        return False
    DATA_DIR.mkdir(exist_ok=True)
    ORDERS_FILE.write_text(json.dumps(kept[:1000], indent=2), encoding="utf-8")
    return True


def expire_provider_checkout(order: dict) -> None:
    """Best-effort remote cancellation before deleting a stale pending order."""
    provider = str(order.get("provider", "")).lower()
    if provider == "stripe" and STRIPE_SECRET_KEY and order.get("provider_session_id"):
        try:
            import stripe
            stripe.api_key = STRIPE_SECRET_KEY
            stripe.checkout.Session.expire(order.get("provider_session_id"))
        except Exception as exc:
            # Stripe may already be completed/expired; local cleanup should still continue.
            logger.info("Could not expire stale Stripe session for %s: %s", order.get("order_id"), exc)


def cleanup_expired_pending_orders() -> int:
    """Delete pending orders that were not paid within the configured window."""
    now = utc_now()
    if using_mongo() and orders_col is not None:
        candidates = list(orders_col.find({"status": "pending"}, {"_id": 0}).limit(500))
    else:
        try:
            candidates = json.loads(ORDERS_FILE.read_text(encoding="utf-8")) if ORDERS_FILE.exists() else []
        except Exception:
            candidates = []
    stale = [o for o in candidates if order_is_expired_pending(o, now)]
    deleted = 0
    for order in stale:
        expire_provider_checkout(order)
        if delete_order(order.get("order_id", "")):
            deleted += 1
    if deleted:
        logger.info("Deleted %s expired pending order(s).", deleted)
    return deleted

def find_order_by_id(order_id: str) -> dict | None:
    order_id = (order_id or "").strip()
    if not order_id:
        return None
    if using_mongo() and orders_col is not None:
        found = orders_col.find_one({"order_id": order_id}, {"_id": 0})
        return found
    return next((o for o in load_orders_for_user(None) if str(o.get("order_id", "")) == order_id), None)

def find_order_by_provider_order(provider_order_id: str) -> dict | None:
    provider_order_id = (provider_order_id or "").strip()
    if not provider_order_id:
        return None
    if using_mongo() and orders_col is not None:
        found = orders_col.find_one({"provider_order_id": provider_order_id}, {"_id": 0})
        return found
    for order in load_orders_for_user(None):
        if str(order.get("provider_order_id", "")).strip() == provider_order_id:
            return order
    return None


# -----------------------------------------------------------------------------
# Purchase-bound Support Tickets
# -----------------------------------------------------------------------------
def normalize_ticket_status(status: str | None) -> str:
    status = str(status or "open").strip().lower()
    return status if status in {"open", "waiting", "answered", "closed"} else "open"


def support_actor_kind(user: dict | None) -> str:
    if has_support_access(user):
        return "staff"
    return "customer"


def ticket_public_id() -> str:
    return "TKT-" + secrets.token_hex(4).upper()


def load_support_tickets(status: str | None = None) -> list:
    query = {}
    if status:
        query["status"] = normalize_ticket_status(status)
    if using_mongo() and support_tickets_col is not None:
        return list(support_tickets_col.find(query, {"_id": 0}).sort("updated_at", -1).limit(200))
    try:
        tickets = json.loads(SUPPORT_TICKETS_FILE.read_text(encoding="utf-8")) if SUPPORT_TICKETS_FILE.exists() else []
    except Exception:
        tickets = []
    if status:
        tickets = [t for t in tickets if normalize_ticket_status(t.get("status")) == normalize_ticket_status(status)]
    return sorted(tickets, key=lambda t: str(t.get("updated_at") or t.get("created_at") or ""), reverse=True)[:200]


def load_support_tickets_for_user(email: str | None) -> list:
    email = (email or "").strip().lower()
    if not email:
        return []
    if using_mongo() and support_tickets_col is not None:
        return list(support_tickets_col.find({"user_email": email}, {"_id": 0}).sort("updated_at", -1).limit(80))
    return [t for t in load_support_tickets() if str(t.get("user_email", "")).lower() == email][:80]


def find_support_ticket(ticket_id: str) -> dict | None:
    ticket_id = (ticket_id or "").strip()
    if not ticket_id:
        return None
    if using_mongo() and support_tickets_col is not None:
        return support_tickets_col.find_one({"ticket_id": ticket_id}, {"_id": 0})
    return next((t for t in load_support_tickets() if str(t.get("ticket_id")) == ticket_id), None)


def save_support_ticket(ticket: dict) -> None:
    ticket["status"] = normalize_ticket_status(ticket.get("status"))
    ticket.setdefault("created_at", utc_now().isoformat())
    ticket["updated_at"] = utc_now().isoformat()
    if using_mongo() and support_tickets_col is not None:
        support_tickets_col.update_one({"ticket_id": ticket["ticket_id"]}, {"$set": ticket}, upsert=True)
        return
    DATA_DIR.mkdir(exist_ok=True)
    try:
        tickets = json.loads(SUPPORT_TICKETS_FILE.read_text(encoding="utf-8")) if SUPPORT_TICKETS_FILE.exists() else []
    except Exception:
        tickets = []
    tickets = [t for t in tickets if str(t.get("ticket_id")) != str(ticket.get("ticket_id"))]
    tickets.insert(0, ticket)
    SUPPORT_TICKETS_FILE.write_text(json.dumps(tickets[:1000], indent=2), encoding="utf-8")


def delete_support_ticket(ticket_id: str) -> bool:
    ticket = find_support_ticket(ticket_id)
    if not ticket:
        return False
    cleanup_support_ticket_files(ticket)
    if using_mongo() and support_tickets_col is not None:
        result = support_tickets_col.delete_one({"ticket_id": ticket_id})
        return bool(getattr(result, "deleted_count", 0))
    try:
        tickets = json.loads(SUPPORT_TICKETS_FILE.read_text(encoding="utf-8")) if SUPPORT_TICKETS_FILE.exists() else []
    except Exception:
        tickets = []
    tickets = [t for t in tickets if str(t.get("ticket_id")) != str(ticket_id)]
    SUPPORT_TICKETS_FILE.write_text(json.dumps(tickets[:1000], indent=2), encoding="utf-8")
    return True


def user_can_view_ticket(ticket: dict, user: dict | None) -> bool:
    if not ticket or not user:
        return False
    if has_support_access(user):
        return True
    return str(ticket.get("user_email", "")).lower() == str(user.get("email", "")).lower()


def order_item_for_ticket(user: dict, order_id: str, item_index: int) -> tuple[dict | None, dict | None, str | None]:
    order = find_order_by_id(order_id)
    if not order:
        return None, None, "That order could not be found."
    if str(order.get("user_email", "")).lower() != str(user.get("email", "")).lower():
        return None, None, "That order is not tied to your account."
    if str(order.get("status", "")).lower() != "paid":
        return None, None, "Support tickets can only be opened after payment is confirmed."
    items = ((order.get("cart") or {}).get("items") or [])
    if item_index < 0 or item_index >= len(items):
        return None, None, "Choose a valid product from that order."
    return order, items[item_index], None


def has_open_ticket_for_order_item(email: str, order_id: str, item_index: int) -> dict | None:
    email = (email or "").strip().lower()
    for t in load_support_tickets_for_user(email):
        if str(t.get("order_id")) == str(order_id) and int(t.get("item_index", -1)) == int(item_index) and normalize_ticket_status(t.get("status")) != "closed":
            return t
    return None


def support_upload_allowed(filename: str) -> bool:
    return allowed_upload(filename, "file")


def save_support_attachments(ticket_id: str, files) -> tuple[list[dict], str | None]:
    saved = []
    file_list = [f for f in (files or []) if getattr(f, "filename", "")]
    if len(file_list) > MAX_SUPPORT_ATTACHMENTS_PER_MESSAGE:
        return [], f"Upload up to {MAX_SUPPORT_ATTACHMENTS_PER_MESSAGE} files per message."
    if not file_list:
        return [], None
    ticket_dir = SUPPORT_UPLOADS_DIR / secure_filename(ticket_id)
    ticket_dir.mkdir(parents=True, exist_ok=True)
    for upload in file_list:
        original = secure_filename(upload.filename or "")
        if not original or not support_upload_allowed(original):
            return [], "One attachment type is not allowed. Use images, txt, pdf, zip, rar, 7z, or json."
        upload.stream.seek(0, os.SEEK_END)
        size = upload.stream.tell()
        upload.stream.seek(0)
        if size > MAX_SUPPORT_ATTACHMENT_MB * 1024 * 1024:
            return [], f"Each support file must be {MAX_SUPPORT_ATTACHMENT_MB}MB or smaller."
        ext = original.rsplit(".", 1)[-1].lower() if "." in original else "bin"
        filename = f"{secrets.token_hex(8)}.{ext}"
        path = ticket_dir / filename
        upload.save(path)
        saved.append({
            "filename": filename,
            "original_name": original[:120],
            "size_bytes": int(size),
            "content_type": mimetypes.guess_type(original)[0] or "application/octet-stream",
            "url": f"/support/attachments/{ticket_id}/{filename}",
        })
    return saved, None


def cleanup_support_ticket_files(ticket: dict) -> None:
    ticket_id = secure_filename(str((ticket or {}).get("ticket_id") or ""))
    if not ticket_id:
        return
    ticket_dir = SUPPORT_UPLOADS_DIR / ticket_id
    if ticket_dir.exists() and ticket_dir.is_dir():
        import shutil
        try:
            shutil.rmtree(ticket_dir)
        except Exception:
            logger.exception("Failed to delete support uploads for %s", ticket_id)


def send_support_ticket_email(to_email: str, ticket: dict, message: str, staff_update: bool = False) -> bool:
    subject = f"Support ticket {ticket.get('ticket_id')} updated"
    title = "Support reply received" if staff_update else "Support ticket updated"
    safe_ticket = html_escape.escape(str(ticket.get("ticket_id", "")))
    safe_subject = html_escape.escape(str(ticket.get("subject", "Support ticket")))
    safe_message = html_escape.escape((message or "").strip()[:1200]).replace("\n", "<br>")
    ticket_url = f"{APP_URL}/support/tickets/{ticket.get('ticket_id')}"
    body_html = f"""
    <p style='margin:0 0 12px;color:#d9c5ff;line-height:1.7;'>Ticket <strong style='color:#fff;'>{safe_ticket}</strong> for <strong style='color:#fff;'>{safe_subject}</strong> has a new update.</p>
    <div style='margin:14px 0;padding:14px;border-radius:16px;background:rgba(255,255,255,.045);border:1px solid rgba(255,255,255,.09);color:#efe7ff;line-height:1.65;'>{safe_message or 'Open your ticket to view the latest update.'}</div>
    """
    text = f"Ticket {ticket.get('ticket_id')} updated\n\n{ticket.get('subject')}\n\n{message}\n\nOpen: {ticket_url}"
    return send_html_email(to_email, subject, text, branded_email_shell(title, "Support ticket", "Your support conversation was updated.", body_html, "Open ticket", ticket_url), "Support")


def send_support_discord_webhook(ticket: dict, message: dict) -> bool:
    if not SUPPORT_TICKET_WEBHOOK_URL or not discord_webhook_allowed(SUPPORT_TICKET_WEBHOOK_URL):
        return False
    actor = message.get("actor") or {}
    attachments = message.get("attachments") or []
    desc = str(message.get("body") or "").strip()[:1500] or "New support update."
    embed = {
        "title": f"Support ticket: {ticket.get('subject') or ticket.get('ticket_id')}",
        "description": desc,
        "color": 0xA855F7,
        "fields": [
            {"name": "Ticket", "value": str(ticket.get("ticket_id", "—")), "inline": True},
            {"name": "Customer", "value": str(ticket.get("user_email", "—")), "inline": True},
            {"name": "Order", "value": str(ticket.get("order_id", "—")), "inline": True},
            {"name": "Product", "value": str(ticket.get("product_name", "—"))[:100], "inline": True},
            {"name": "Sender", "value": f"{actor.get('username') or 'user'} ({actor.get('email') or 'unknown'})", "inline": False},
        ],
        "timestamp": utc_now().isoformat(),
        "footer": {"text": f"{APP_NAME} Support"},
    }
    if attachments:
        embed["fields"].append({"name": "Attachments", "value": "\n".join(f"[{a.get('original_name')}]({APP_URL}{a.get('url')})" for a in attachments[:5]), "inline": False})
    payload = {"username": f"{APP_NAME} Support", "embeds": [embed]}
    try:
        resp = requests.post(SUPPORT_TICKET_WEBHOOK_URL, json=payload, timeout=12)
        return resp.status_code in (200, 204)
    except Exception:
        logger.exception("Support ticket webhook failed for %s", ticket.get("ticket_id"))
        return False


def append_ticket_message(ticket: dict, user: dict, body: str, attachments: list[dict] | None = None) -> dict:
    actor_kind = support_actor_kind(user)
    msg = {
        "id": secrets.token_urlsafe(10),
        "body": (body or "").strip()[:3000],
        "actor_kind": actor_kind,
        "actor": sanitize_user_for_session(user) or {},
        "attachments": attachments or [],
        "created_at": utc_now().isoformat(),
    }
    ticket.setdefault("messages", []).append(msg)
    ticket["last_message_by"] = actor_kind
    if normalize_ticket_status(ticket.get("status")) != "closed":
        ticket["status"] = "answered" if actor_kind == "staff" else "waiting"
    save_support_ticket(ticket)
    # Discord webhook only for customer/non-staff messages, so it notifies you without admin/support reply spam.
    if actor_kind != "staff":
        send_support_discord_webhook(ticket, msg)
        send_support_ticket_email(OWNER_EMAIL, ticket, msg.get("body"), staff_update=False)
    else:
        send_support_ticket_email(ticket.get("user_email"), ticket, msg.get("body"), staff_update=True)
    return msg

def order_amount_matches(order: dict, paid_cents: int | None, currency: str | None = None) -> bool:
    if paid_cents is None:
        return False
    if str(currency or order.get("currency", "")).upper() != str(order.get("currency", "")).upper():
        return False
    return int(paid_cents) == int(order.get("amount_cents") or 0)


def extract_paypal_paid_amount_cents(data: dict) -> tuple[int | None, str | None]:
    try:
        units = data.get("purchase_units") or []
        captures = (((units[0] or {}).get("payments") or {}).get("captures") or []) if units else []
        amount = (captures[0] or {}).get("amount") or {} if captures else {}
        return money_to_cents(amount.get("value")), amount.get("currency_code")
    except Exception:
        return None, None


ORDER_STATUSES = {"pending", "paid", "refunded", "cancelled", "failed", "chargeback"}
FINAL_ADMIN_ORDER_STATUSES = {"refunded", "cancelled", "chargeback"}


def normalize_order_status(value: str) -> str:
    status = str(value or "").strip().lower()
    return status if status in ORDER_STATUSES else ""


def update_order_status(order_id: str, status: str, details: dict | None = None):
    order_id = (order_id or "").strip()
    status = normalize_order_status(status)
    if not order_id or not status:
        logger.warning("Ignoring invalid order status update: order=%s status=%s", order_id, status)
        return None
    orders = load_orders_for_user(None)
    existing = next((o for o in orders if o.get("order_id") == order_id), None)
    if not existing:
        logger.warning("Ignoring status update for missing/deleted order %s -> %s", order_id, status)
        return None
    details = dict(details or {})
    source = str(details.get("status_source") or "provider").lower()
    if order_is_expired_pending(existing) and status != "paid" and source != "admin":
        delete_order(order_id)
        return None
    previous_status = normalize_order_status(existing.get("status")) or "pending"
    if previous_status in FINAL_ADMIN_ORDER_STATUSES and status == "paid" and source != "admin":
        logger.warning("Blocked delayed provider update for finalized order %s (%s -> paid)", order_id, previous_status)
        return existing
    was_notified = bool(existing.get("owner_notified_at"))
    existing["status"] = status
    existing["updated_at"] = utc_now().isoformat()
    if details:
        existing.update(details)
    if str(status).lower() == "paid":
        if previous_status != "paid":
            increment_discount_code_usage(existing.get("cart") or {})
        existing = process_auto_delivery(existing)
    should_notify_owner = str(status).lower() == "paid" and not was_notified
    if should_notify_owner:
        existing["owner_notified_at"] = utc_now().isoformat()
    save_order(existing)
    if should_notify_owner:
        send_owner_order_webhook(existing)
    return existing


def paypal_config_hint() -> str:
    mode = (PAYPAL_MODE or "sandbox").lower()
    return (
        f"PayPal {mode} credentials were rejected. "
        f"Make sure PAYPAL_MODE={mode} matches the Client ID and Secret you copied. "
        "Sandbox mode requires Sandbox REST app credentials. Live mode requires Live REST app credentials. "
        "After editing .env, fully stop and restart Flask."
    )


def paypal_access_token() -> str:
    if not PAYPAL_CLIENT_ID or not PAYPAL_CLIENT_SECRET:
        raise RuntimeError("PayPal credentials are not configured. Add PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET to .env.")
    try:
        resp = requests.post(
            f"{PAYPAL_API_BASE}/v1/oauth2/token",
            auth=(PAYPAL_CLIENT_ID.strip(), PAYPAL_CLIENT_SECRET.strip()),
            data={"grant_type": "client_credentials"},
            timeout=15,
        )
    except requests.RequestException as exc:
        raise RuntimeError(f"Could not reach PayPal {PAYPAL_MODE} API. Check your internet/network and PAYPAL_MODE. {exc}") from exc

    if resp.status_code == 401:
        raise RuntimeError(paypal_config_hint())
    if resp.status_code >= 400:
        body = resp.text[:240].replace("\n", " ")
        raise RuntimeError(f"PayPal token request failed ({resp.status_code}). {body}")
    data = resp.json()
    token = data.get("access_token")
    if not token:
        raise RuntimeError("PayPal did not return an access token. Recheck your REST app credentials.")
    return token

def get_allowed_download_files() -> set[str]:
    """
    Build a whitelist of downloadable filenames from products.json.
    This lets free products and paid products share the same secure download system.
    """
    allowed_files = set()

    for product in load_products():
        if not is_download_product(product):
            continue

        downloads = product.get("downloads", {})
        if not isinstance(downloads, dict):
            continue

        download_url = str(downloads.get("downloadUrl", "")).strip()
        if not download_url.startswith("/download/"):
            continue

        filename = download_url.replace("/download/", "", 1).strip("/")
        if filename:
            allowed_files.add(filename)

    return allowed_files




# -----------------------------------------------------------------------------
# Logged-in guides / knowledge base
# -----------------------------------------------------------------------------
def default_guides_settings() -> dict:
    now = utc_now().isoformat()
    return {
        "enabled": True,
        "items": [
            {
                "id": "getting-started",
                "slug": "getting-started",
                "title": "Getting started",
                "summary": "Set up your account, find your purchases, and learn where keys and downloads appear.",
                "category": "Start here",
                "icon": "fa-rocket",
                "badge": "Recommended",
                "sort_order": 10,
                "enabled": True,
                "body": "Sign in with the same email used at checkout.\nOpen your Account dashboard to confirm your order status.\nWhen an order is paid, your key or download appears inside the matching order.\nUse Purchase Support from the order if you need help.",
                "created_at": now,
                "updated_at": now,
            },
            {
                "id": "downloads-and-installation",
                "slug": "downloads-and-installation",
                "title": "Downloads & installation",
                "summary": "A clean checklist for downloading files and preparing your system before installation.",
                "category": "Setup",
                "icon": "fa-download",
                "badge": "Setup",
                "sort_order": 20,
                "enabled": True,
                "body": "Open Downloads from your account or the site navigation.\nDownload only the file connected to your purchased product.\nRead any product-specific notes shown beside the download.\nKeep your key private and never share your account login.\nIf a download is missing, open a support ticket tied to the paid order.",
                "created_at": now,
                "updated_at": now,
            },
            {
                "id": "orders-keys-support",
                "slug": "orders-keys-support",
                "title": "Orders, keys & support",
                "summary": "Understand order statuses, key delivery, refunds, and the fastest way to get support.",
                "category": "Account",
                "icon": "fa-key",
                "badge": "Account",
                "sort_order": 30,
                "enabled": True,
                "body": "Pending means the payment is still being confirmed.\nPaid means the order is complete and delivery can begin.\nRefunded means the payment was returned and access may be removed.\nDelivered keys are stored inside the matching order in your Account page.\nFor help, create a purchase ticket from the paid order so support receives the correct product and order details.",
                "created_at": now,
                "updated_at": now,
            },
        ],
    }


def clean_guide_slug(value: str) -> str:
    return (clean_slug(value or "guide")[:80] or f"guide-{int(utc_now().timestamp())}")


def normalize_guide(data: dict) -> dict:
    if not isinstance(data, dict):
        data = {}
    title = str(data.get("title") or "Guide").strip()[:120] or "Guide"
    slug = clean_guide_slug(data.get("slug") or title)
    icon = str(data.get("icon") or "fa-book-open").strip()[:60]
    if not re.fullmatch(r"fa-[a-z0-9-]+", icon):
        icon = "fa-book-open"
    try:
        sort_order = max(0, min(9999, int(data.get("sort_order", 100))))
    except (TypeError, ValueError):
        sort_order = 100
    return {
        "id": str(data.get("id") or slug or secrets.token_hex(6))[:80],
        "slug": slug,
        "title": title,
        "summary": str(data.get("summary") or "").strip()[:300],
        "category": str(data.get("category") or "General").strip()[:60] or "General",
        "icon": icon,
        "badge": str(data.get("badge") or "").strip()[:40],
        "sort_order": sort_order,
        "enabled": bool(data.get("enabled", True)),
        "body": str(data.get("body") or "").strip()[:12000],
        "created_at": str(data.get("created_at") or utc_now().isoformat()),
        "updated_at": str(data.get("updated_at") or utc_now().isoformat()),
    }


def get_guides_settings() -> dict:
    raw = load_site_settings().get("guides")
    defaults = default_guides_settings()
    if isinstance(raw, dict):
        defaults.update(raw)
        if "items" not in raw:
            defaults["items"] = default_guides_settings()["items"]
    defaults["items"] = [normalize_guide(item) for item in defaults.get("items") or [] if isinstance(item, dict)]
    return defaults


def save_guides_settings(guides_settings: dict) -> None:
    settings = load_site_settings()
    merged = {"enabled": True, "items": []}
    if isinstance(guides_settings, dict):
        merged.update(guides_settings)
    merged["items"] = [normalize_guide(item) for item in merged.get("items") or []]
    settings["guides"] = merged
    save_site_settings(settings)


def visible_guides(include_disabled: bool = False) -> list[dict]:
    data = get_guides_settings()
    items = data.get("items") or []
    if not include_disabled:
        if not data.get("enabled", True):
            return []
        items = [item for item in items if item.get("enabled")]
    return sorted(items, key=lambda item: (item.get("sort_order", 100), item.get("title", "").lower()))


def find_guide(slug: str, include_disabled: bool = False) -> dict | None:
    slug = clean_guide_slug(slug)
    for item in visible_guides(include_disabled=include_disabled):
        if item.get("slug") == slug or item.get("id") == slug:
            return item
    return None


def guide_steps(guide: dict) -> list[str]:
    return [line.strip().lstrip("-• ") for line in str((guide or {}).get("body") or "").splitlines() if line.strip()]

# -----------------------------------------------------------------------------
# Applications system
# -----------------------------------------------------------------------------
def default_application_question() -> dict:
    return {"id": secrets.token_hex(4), "label": "Question", "type": "text", "required": True, "choices": [], "placeholder": ""}


def default_applications_settings() -> dict:
    return {"enabled": True, "items": [], "submissions": []}


def clean_application_slug(value: str) -> str:
    value = clean_slug(value or "application")
    return value[:70] or f"application-{int(utc_now().timestamp())}"


def normalize_application_question(q: dict) -> dict:
    if not isinstance(q, dict):
        q = {}
    qtype = str(q.get("type") or "text").strip().lower()
    allowed = {"text", "textarea", "email", "number", "url", "select", "radio", "checkbox", "image_url"}
    if qtype not in allowed:
        qtype = "text"
    choices = q.get("choices") or []
    if isinstance(choices, str):
        choices = [c.strip() for c in choices.split(",") if c.strip()]
    choices = [str(c).strip()[:80] for c in choices if str(c).strip()][:20]
    label = str(q.get("label") or "Question").strip()[:120] or "Question"
    return {
        "id": str(q.get("id") or clean_slug(label) or secrets.token_hex(4))[:80],
        "label": label,
        "type": qtype,
        "required": bool(q.get("required", True)),
        "choices": choices,
        "placeholder": str(q.get("placeholder") or "").strip()[:160],
        "help": str(q.get("help") or "").strip()[:220],
    }


def parse_application_questions_text(raw: str) -> list[dict]:
    """Admin textarea format: Label|type|required|choice1,choice2|placeholder|help"""
    questions = []
    for line in str(raw or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("|")]
        label = parts[0] if parts else "Question"
        qtype = parts[1] if len(parts) > 1 and parts[1] else "text"
        req_raw = (parts[2] if len(parts) > 2 else "yes").lower()
        required = req_raw in {"yes", "true", "1", "required", "y"}
        choices = parts[3] if len(parts) > 3 else ""
        placeholder = parts[4] if len(parts) > 4 else ""
        help_text = parts[5] if len(parts) > 5 else ""
        questions.append(normalize_application_question({
            "id": clean_slug(label)[:50] or secrets.token_hex(4),
            "label": label,
            "type": qtype,
            "required": required,
            "choices": choices,
            "placeholder": placeholder,
            "help": help_text,
        }))
    return questions[:40]


def application_questions_to_text(questions: list[dict]) -> str:
    lines = []
    for q in questions or []:
        q = normalize_application_question(q)
        required = "yes" if q.get("required") else "no"
        choices = ", ".join(q.get("choices") or [])
        lines.append(f"{q.get('label')}|{q.get('type')}|{required}|{choices}|{q.get('placeholder') or ''}|{q.get('help') or ''}")
    return "\n".join(lines)


def normalize_application(app_data: dict) -> dict:
    if not isinstance(app_data, dict):
        app_data = {}
    title = str(app_data.get("title") or app_data.get("name") or "Application").strip()[:120] or "Application"
    slug = clean_application_slug(app_data.get("slug") or title)
    questions = [normalize_application_question(q) for q in (app_data.get("questions") or []) if isinstance(q, dict)]
    return {
        "id": str(app_data.get("id") or slug or secrets.token_hex(8))[:80],
        "title": title,
        "slug": slug,
        "enabled": bool(app_data.get("enabled", True)),
        "featured": bool(app_data.get("featured", False)),
        "image": str(app_data.get("image") or "").strip()[:500],
        "badge": str(app_data.get("badge") or "Open").strip()[:40] or "Open",
        "short_description": str(app_data.get("short_description") or app_data.get("description") or "").strip()[:260],
        "description": str(app_data.get("description") or app_data.get("short_description") or "").strip()[:3000],
        "instructions": str(app_data.get("instructions") or "").strip()[:2000],
        "webhook_url": str(app_data.get("webhook_url") or "").strip()[:600],
        "success_message": str(app_data.get("success_message") or "Your application was submitted successfully.").strip()[:300] or "Your application was submitted successfully.",
        "questions": questions,
        "created_at": str(app_data.get("created_at") or utc_now().isoformat()),
        "updated_at": str(app_data.get("updated_at") or utc_now().isoformat()),
    }


def get_applications_settings() -> dict:
    raw = load_site_settings().get("applications")
    defaults = default_applications_settings()
    if isinstance(raw, dict):
        defaults.update(raw)
    defaults["items"] = [normalize_application(a) for a in defaults.get("items") or [] if isinstance(a, dict)]
    defaults["submissions"] = list(defaults.get("submissions") or [])[-200:]
    return defaults


def save_applications_settings(apps_settings: dict) -> None:
    settings = load_site_settings()
    merged = default_applications_settings()
    if isinstance(apps_settings, dict):
        merged.update(apps_settings)
    merged["items"] = [normalize_application(a) for a in merged.get("items") or []]
    merged["submissions"] = list(merged.get("submissions") or [])[-200:]
    settings["applications"] = merged
    save_site_settings(settings)


def visible_applications(include_disabled: bool = False) -> list[dict]:
    apps = get_applications_settings()
    items = apps.get("items") or []
    if not include_disabled:
        items = [a for a in items if a.get("enabled")]
    return sorted(items, key=lambda a: (not a.get("featured"), a.get("title", "").lower()))


def find_application(slug: str, include_disabled: bool = False) -> dict | None:
    slug = clean_application_slug(slug)
    for app_data in visible_applications(include_disabled=include_disabled):
        if app_data.get("slug") == slug or app_data.get("id") == slug:
            return app_data
    return None


def discord_webhook_allowed(url: str) -> bool:
    try:
        parsed = urlparse(url or "")
        host = (parsed.netloc or "").lower()
        return parsed.scheme == "https" and host in {"discord.com", "discordapp.com"} and parsed.path.startswith("/api/webhooks/")
    except Exception:
        return False


def send_application_webhook(app_data: dict, submission: dict) -> bool:
    webhook_url = (app_data or {}).get("webhook_url") or ""
    if not webhook_url:
        return False
    if not discord_webhook_allowed(webhook_url):
        logger.warning("Rejected invalid application webhook URL for %s", app_data.get("slug"))
        return False
    user = submission.get("user") or {}
    answer_lines = []
    for ans in submission.get("answers") or []:
        label = str(ans.get("label") or "Question")[:120]
        value = ans.get("value")
        if isinstance(value, list):
            value = ", ".join(str(v) for v in value)
        value = str(value or "").strip()[:900] or "—"
        answer_lines.append({"name": label, "value": value, "inline": False})
    embed = {
        "title": f"New application: {app_data.get('title', 'Application')}",
        "description": f"Submitted by **{user.get('username') or 'user'}** (`{user.get('email') or 'unknown'}`)",
        "color": 0x9333EA,
        "fields": answer_lines[:25],
        "footer": {"text": f"{APP_NAME} Applications"},
        "timestamp": utc_now().isoformat(),
    }
    payload = {"username": f"{APP_NAME} Applications", "embeds": [embed]}
    try:
        resp = requests.post(webhook_url, json=payload, timeout=12)
        if resp.status_code in (200, 204):
            return True
        logger.warning("Application webhook failed for %s: %s %s", app_data.get("slug"), resp.status_code, resp.text[:160])
    except Exception:
        logger.exception("Application webhook request failed for %s", app_data.get("slug"))
    return False


def build_application_submission(app_data: dict, user: dict, form) -> tuple[dict | None, str | None]:
    answers = []
    for q in app_data.get("questions") or []:
        q = normalize_application_question(q)
        field = f"q_{q.get('id')}"
        if q.get("type") == "checkbox":
            value = [v.strip()[:160] for v in form.getlist(field) if v.strip()]
            missing = q.get("required") and not value
        else:
            value = str(form.get(field, "")).strip()
            if q.get("type") == "email" and value and not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", value):
                return None, f"{q.get('label')} must be a valid email."
            if q.get("type") == "url" and value and not re.match(r"^https?://", value, re.I):
                return None, f"{q.get('label')} must start with http:// or https://."
            if q.get("type") == "image_url" and value and not re.match(r"^https?://", value, re.I):
                return None, f"{q.get('label')} must be an image URL starting with http:// or https://."
            value = value[:2000]
            missing = q.get("required") and not value
        if missing:
            return None, f"{q.get('label')} is required."
        answers.append({"id": q.get("id"), "label": q.get("label"), "type": q.get("type"), "value": value})
    submission = {
        "id": secrets.token_urlsafe(12),
        "application_id": app_data.get("id"),
        "application_slug": app_data.get("slug"),
        "application_title": app_data.get("title"),
        "user": sanitize_user_for_session(user) or {},
        "answers": answers,
        "webhook_sent": False,
        "created_at": utc_now().isoformat(),
        "ip_hint": get_remote_address(),
    }
    return submission, None


def default_discount_settings() -> dict:
    return {
        "enabled": True,
        "store_auto": {"enabled": False, "label": "Store discount", "type": "percent", "value": 0, "min_subtotal": 0},
        "bulk": {"enabled": False, "label": "Bulk discount", "type": "percent", "value": 0, "min_quantity": 2, "min_subtotal": 0},
        "discord": {"enabled": False, "label": "Discord linked discount", "type": "percent", "value": 0, "min_subtotal": 0},
        "codes": [],
    }


def default_site_settings() -> dict:
    return {
        "maintenance_enabled": False,
        "store_enabled": True,
        "registration_enabled": True,
        "announcement_enabled": False,
        "announcement_text": "",
        "announcement_type": "info",
        "site_name": APP_NAME,
        "site_tagline": "Premium digital products, tools, and support.",
        "site_url": APP_URL,
        "support_email": SUPPORT_EMAIL,
        "support_url": "/support",
        "discord_url": "",
        "store_url": "/store",
        "downloads_url": "/downloads",
        "status_url": "/status",
        "applications_url": "/applications",
        "legal_url": "/legal",
        "discounts": default_discount_settings(),
        "applications": default_applications_settings(),
        "updated_at": utc_now().isoformat(),
    }


def load_site_settings() -> dict:
    settings = default_site_settings()
    try:
        if using_mongo() and settings_col is not None:
            found = settings_col.find_one({"key": "site"}, {"_id": 0}) or {}
            data = found.get("value", {}) if isinstance(found.get("value", {}), dict) else {}
            settings.update(data)
            return settings
        if SETTINGS_FILE.exists():
            data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                settings.update(data)
    except Exception:
        logger.exception("Could not load site settings")
    return settings


def save_site_settings(settings: dict) -> None:
    merged = default_site_settings()
    if isinstance(settings, dict):
        merged.update(settings)
    merged["updated_at"] = utc_now().isoformat()
    if using_mongo() and settings_col is not None:
        settings_col.update_one({"key": "site"}, {"$set": {"key": "site", "value": merged}}, upsert=True)
        return
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(merged, indent=2), encoding="utf-8")


def is_maintenance_mode() -> bool:
    if os.getenv("MAINTENANCE_MODE", "false").lower() == "true":
        return True
    return bool(load_site_settings().get("maintenance_enabled"))


# -----------------------------------------------------------------------------
# Template globals
# -----------------------------------------------------------------------------
@app.context_processor
def inject_global_template_vars():
    return {
        "current_year": datetime.now().year,
        "current_user": current_user(),
        "csrf_token": csrf_token,
        "discord_oauth_enabled": discord_oauth_ready(),
        "discord_oauth_missing": discord_oauth_missing(),
        "discord_bot_enabled": bool(DISCORD_BOT_TOKEN),
        "discord_guild_join_enabled": discord_guild_join_ready(),
        "discord_guild_id": DISCORD_GUILD_ID,
        "google_oauth_enabled": google_oauth_ready(),
        "google_oauth_missing": google_oauth_missing(),
        "mongo_status_reason": mongo_status_reason,
        "stripe_enabled": bool(STRIPE_SECRET_KEY),
        "paypal_enabled": bool(PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET),
        "paypal_client_id": PAYPAL_CLIENT_ID,
        "paypal_mode": PAYPAL_MODE,
        "processing_fee_percent": PROCESSING_FEE_PERCENT,
        "checkout_currency": STRIPE_CURRENCY.upper(),
        "pending_order_max_age_minutes": PENDING_ORDER_MAX_AGE_MINUTES,
        "site_settings": load_site_settings(),
        "using_mongo": using_mongo(),
        "application_questions_to_text": application_questions_to_text,
        "support_webhook_configured": bool(SUPPORT_TICKET_WEBHOOK_URL),
    }


# -----------------------------------------------------------------------------
# Request hooks
# -----------------------------------------------------------------------------
@app.before_request
def cleanup_stale_pending_orders_before_request():
    # Render/Flask apps do not always have a background worker. Running a tiny
    # cleanup on normal requests keeps abandoned pending orders out of user/admin panels.
    if request.path.startswith(("/static/", "/media/", "/webhooks/")):
        return None
    try:
        cleanup_expired_pending_orders()
    except Exception as exc:
        logger.warning("Pending order cleanup failed: %s", exc)
    return None


@app.before_request
def set_session_timeout():
    session.permanent = True
    user = current_user()
    if user and using_mongo():
        fresh = get_user_by_email(user.get("email"))
        if not fresh or fresh.get("status") == "suspended":
            session.clear()
            abort(403, description="This account is not allowed to access the site.")
        session["user"] = sanitize_user_for_session(fresh)
    if request.method == "POST" and not request.path.startswith("/api/") and not request.path.startswith("/webhooks/"):
        if not verify_csrf():
            abort(403, description="Security token expired. Refresh the page and try again.")


@app.before_request
def check_maintenance():
    if not is_maintenance_mode():
        return None

    allowed_paths = {
        "/maintenance",
        "/health",
        "/login",
        "/logout",
        "/verify-email",
        "/forgot-password",
    }

    if request.path.startswith(("/static/", "/media/", "/auth/discord", "/auth/google", "/webhooks/")):
        return None

    user = current_user() or {}
    if has_admin_access(user) or has_support_access(user):
        return None

    if request.path not in allowed_paths:
        return render_template("maintenance.html", active_page=None), 503

    return None


@app.after_request
def apply_security_headers(response):
    response.headers["Content-Security-Policy"] = CSP_POLICY
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["X-Download-Options"] = "noopen"
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin-allow-popups"
    response.headers["Cross-Origin-Resource-Policy"] = "same-site"
    if request.path.startswith("/static/"):
        response.headers["Cache-Control"] = "public, max-age=604800, stale-while-revalidate=86400"
    elif request.path.startswith(("/admin", "/account", "/checkout")):
        response.headers["Cache-Control"] = "no-store, private"

    if IS_PRODUCTION and request.is_secure:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains; preload"

    return response


# -----------------------------------------------------------------------------
# Error handlers
# -----------------------------------------------------------------------------
@app.errorhandler(404)
def page_not_found(error):
    return render_template("404.html", active_page=None), 404


@app.errorhandler(403)
def forbidden(error):
    return render_template("403.html", error=getattr(error, "description", "Forbidden"), active_page=None), 403


@app.errorhandler(429)
def ratelimit_handler(error):
    return render_template("403.html", error="Too many requests. Please slow down.", active_page=None), 429


@app.errorhandler(500)
def internal_error(error):
    logger.exception("Internal server error: %s", error)
    return render_template("500.html", active_page=None), 500


# -----------------------------------------------------------------------------
# Main pages
# -----------------------------------------------------------------------------
@app.route("/")
def home():
    return render_template("index.html", active_page="home")


@app.route("/store")
@app.route("/products")
def store():
    if not load_site_settings().get("store_enabled", True):
        flash("The store is temporarily closed. Please check back shortly.", "warning")
        return redirect(url_for("home"))
    return render_template("store.html", active_page="products")

@app.route("/cart")
def cart_page():
    return render_template("cart.html", active_page="products")

@app.route("/checkout")
@login_required
def checkout():
    if not load_site_settings().get("store_enabled", True):
        flash("Checkout is temporarily closed.", "warning")
        return redirect(url_for("cart"))
    return render_template("checkout.html", active_page="products")

@app.route("/checkout/success")
@login_required
def checkout_success():
    cleanup_expired_pending_orders()
    order_id = request.args.get("order_id", "")
    stripe_session_id = request.args.get("session_id", "")
    if stripe_session_id and STRIPE_SECRET_KEY:
        try:
            import stripe
            stripe.api_key = STRIPE_SECRET_KEY
            checkout_session = stripe.checkout.Session.retrieve(stripe_session_id)
            if object_get(checkout_session, "payment_status") == "paid":
                metadata = object_get(checkout_session, "metadata", {}) or {}
                resolved_order_id = order_id or object_get(metadata, "order_id", "")
                order = find_order_by_id(resolved_order_id)
                paid_cents = object_get(checkout_session, "amount_total")
                paid_currency = str(object_get(checkout_session, "currency", "")).upper()
                if order and str(order.get("provider_session_id", "")) == str(stripe_session_id) and str(order.get("user_email", "")).lower() == str((current_user() or {}).get("email", "")).lower() and order_amount_matches(order, paid_cents, paid_currency):
                    update_order_status(resolved_order_id, "paid", {"provider_payment_id": stripe_session_id})
                    flash("Payment confirmed. Your order is ready in your account.", "success")
                else:
                    logger.warning("Rejected Stripe success verification for order=%s session=%s", resolved_order_id, stripe_session_id)
        except Exception as exc:
            logger.warning("Could not verify Stripe success session: %s", exc)
    return render_template("checkout_success.html", active_page="products", order_id=order_id)


@app.route("/product/<slug>")
@app.route("/store/<slug>")
def product_detail(slug):
    product = find_store_product(slug)

    if not product:
        abort(404)

    return render_template("product_detail.html", product=product, active_page="products")

@app.route("/downloads")
def downloads():
    return render_template("downloads.html", active_page="downloads")


@app.route("/status")
def status():
    return render_template("status.html", active_page="status")


@app.route("/support")
def support():
    if not current_user():
        flash("Log in to create purchase support tickets.", "warning")
        return redirect(url_for("login", next=request.path))
    user = current_user() or {}
    if has_support_access(user) and request.args.get("staff") == "1":
        return redirect(url_for("support_staff"))
    orders = [o for o in load_orders_for_user(user.get("email")) if str(o.get("status", "")).lower() == "paid"]
    tickets = load_support_tickets_for_user(user.get("email"))
    return render_template("support.html", active_page="support", orders=orders, tickets=tickets)


@app.route("/support/tickets", methods=["POST"])
@login_required
@limiter.limit("8 per hour")
def support_ticket_create():
    user = current_user() or {}
    try:
        item_index = int(request.form.get("item_index", "0"))
    except ValueError:
        item_index = 0
    order_id = request.form.get("order_id", "").strip()
    subject = request.form.get("subject", "").strip()[:120]
    body = request.form.get("message", "").strip()[:3000]
    if len(body) < 8:
        flash("Add a short message explaining what you need help with.", "warning")
        return redirect(url_for("support"))
    order, item, error = order_item_for_ticket(user, order_id, item_index)
    if error:
        flash(error, "danger")
        return redirect(url_for("support"))
    existing = has_open_ticket_for_order_item(user.get("email"), order_id, item_index)
    if existing:
        flash("You already have an open ticket for that exact purchase. Add a reply there instead.", "warning")
        return redirect(url_for("support_ticket_detail", ticket_id=existing.get("ticket_id")))
    ticket_id = ticket_public_id()
    attachments, upload_error = save_support_attachments(ticket_id, request.files.getlist("attachments"))
    if upload_error:
        cleanup_support_ticket_files({"ticket_id": ticket_id})
        flash(upload_error, "danger")
        return redirect(url_for("support"))
    ticket = {
        "ticket_id": ticket_id,
        "status": "open",
        "priority": "normal",
        "subject": subject or f"Support for {item.get('product_name', 'purchase')}",
        "user_email": user.get("email"),
        "user": sanitize_user_for_session(user) or {},
        "order_id": order.get("order_id"),
        "provider": order.get("provider"),
        "item_index": item_index,
        "product_name": item.get("product_name", "Product"),
        "option_name": item.get("option_name", "Option"),
        "product_image": item.get("image", "/static/logo.png"),
        "created_at": utc_now().isoformat(),
        "messages": [],
    }
    save_support_ticket(ticket)
    append_ticket_message(ticket, user, body, attachments)
    record_audit("support.ticket_created", ticket_id, {"order_id": order_id, "item_index": item_index})
    flash("Support ticket created. We’ll reply here and by email.", "success")
    return redirect(url_for("support_ticket_detail", ticket_id=ticket_id))


@app.route("/support/tickets/<ticket_id>")
@login_required
def support_ticket_detail(ticket_id):
    ticket = find_support_ticket(ticket_id)
    user = current_user() or {}
    if not user_can_view_ticket(ticket, user):
        abort(404)
    return render_template("ticket_detail.html", active_page="support", ticket=ticket, staff_view=has_support_access(user))


@app.route("/support/tickets/<ticket_id>/reply", methods=["POST"])
@login_required
@limiter.limit("20 per hour")
def support_ticket_reply(ticket_id):
    ticket = find_support_ticket(ticket_id)
    user = current_user() or {}
    if not user_can_view_ticket(ticket, user):
        abort(404)
    if normalize_ticket_status(ticket.get("status")) == "closed" and not has_support_access(user):
        flash("This ticket is closed. Open a new purchase ticket if you still need help.", "warning")
        return redirect(url_for("support_ticket_detail", ticket_id=ticket_id))
    body = request.form.get("message", "").strip()[:3000]
    if len(body) < 2:
        flash("Type a message before sending.", "warning")
        return redirect(url_for("support_ticket_detail", ticket_id=ticket_id))
    attachments, upload_error = save_support_attachments(ticket_id, request.files.getlist("attachments"))
    if upload_error:
        flash(upload_error, "danger")
        return redirect(url_for("support_ticket_detail", ticket_id=ticket_id))
    if normalize_ticket_status(ticket.get("status")) == "closed" and has_support_access(user):
        ticket["status"] = "open"
    append_ticket_message(ticket, user, body, attachments)
    record_audit("support.ticket_reply", ticket_id, {"actor": user.get("email")})
    flash("Reply sent.", "success")
    return redirect(url_for("support_ticket_detail", ticket_id=ticket_id))


@app.route("/support/tickets/<ticket_id>/status", methods=["POST"])
@support_required
def support_ticket_status(ticket_id):
    ticket = find_support_ticket(ticket_id)
    if not ticket:
        abort(404)
    ticket["status"] = normalize_ticket_status(request.form.get("status"))
    save_support_ticket(ticket)
    record_audit("support.ticket_status", ticket_id, {"status": ticket["status"]})
    if ticket["status"] == "closed":
        send_support_ticket_email(ticket.get("user_email"), ticket, "Your support ticket was closed. Reply is disabled, but you can open a new purchase ticket if needed.", staff_update=True)
    flash("Ticket status updated.", "success")
    return redirect(request.referrer or url_for("support_staff"))


@app.route("/support/tickets/<ticket_id>/delete", methods=["POST"])
@support_required
def support_ticket_delete(ticket_id):
    if delete_support_ticket(ticket_id):
        record_audit("support.ticket_deleted", ticket_id, {})
        flash("Ticket and its uploaded files were deleted.", "success")
    else:
        flash("Ticket was not found.", "warning")
    return redirect(url_for("support_staff"))


@app.route("/support/staff")
@support_required
def support_staff():
    status = request.args.get("status") or ""
    tickets = load_support_tickets(status if status else None)
    return render_template("support_staff.html", active_page="support", tickets=tickets, status=status)


@app.route("/support/attachments/<ticket_id>/<filename>")
@login_required
def support_attachment(ticket_id, filename):
    ticket = find_support_ticket(ticket_id)
    if not user_can_view_ticket(ticket, current_user()):
        abort(404)
    safe_ticket = secure_filename(ticket_id)
    safe_name = secure_filename(filename)
    if not safe_ticket or not safe_name:
        abort(404)
    directory = SUPPORT_UPLOADS_DIR / safe_ticket
    if not (directory / safe_name).exists():
        abort(404)
    return send_from_directory(directory, safe_name, as_attachment=False)


@app.route("/maintenance")
def maintenance():
    return render_template("maintenance.html", active_page=None), 503

@app.route("/donate")
def donate():
    return render_template("donate.html", active_page="donate")


@app.route("/discord")
def discord():
    return render_template("discord.html", active_page="discord")


@app.route("/applications")
def applications_page():
    apps = visible_applications(include_disabled=False)
    return render_template("applications.html", applications=apps, active_page="applications")


@app.route("/applications/<slug>", methods=["GET", "POST"])
@limiter.limit("8 per minute", methods=["POST"])
def application_detail(slug):
    app_data = find_application(slug, include_disabled=False)
    if not app_data:
        abort(404)
    if request.method == "POST":
        user = current_user()
        if not user:
            flash("Log in to submit an application.", "warning")
            return redirect(url_for("login", next=request.path))
        submission, error = build_application_submission(app_data, user, request.form)
        if error:
            flash(error, "danger")
            return render_template("application_detail.html", application=app_data, active_page="applications")
        webhook_sent = send_application_webhook(app_data, submission)
        submission["webhook_sent"] = webhook_sent
        apps = get_applications_settings()
        submissions = list(apps.get("submissions") or [])
        submissions.append(submission)
        apps["submissions"] = submissions[-200:]
        save_applications_settings(apps)
        record_audit("application_submitted", app_data.get("slug", ""), {"user": user.get("email"), "webhook_sent": webhook_sent})
        flash(app_data.get("success_message") or "Application submitted.", "success")
        return redirect(url_for("applications_page"))
    return render_template("application_detail.html", application=app_data, active_page="applications")


# -----------------------------------------------------------------------------
# Auth / Owner dashboard
# -----------------------------------------------------------------------------
@app.route("/signup", methods=["GET", "POST"])
@limiter.limit("12 per minute")
def signup():
    if not load_site_settings().get("registration_enabled", True):
        flash("New account registration is temporarily disabled.", "warning")
        return redirect(url_for("login"))
    if request.method == "POST":
        code = request.form.get("code", "").strip()
        if code and session.get("pending_signup"):
            payload = consume_email_code_flow("signup", code)
            if not payload:
                flash("Invalid or expired security code.", "danger")
                return render_template("verify_email.html", flow="signup", email=session.get("pending_signup", {}).get("email"), active_page="signup")
            if not using_mongo():
                flash("MongoDB is required for customer accounts.", "danger")
                return redirect(url_for("login"))
            try:
                users_col.insert_one({
                    "email": payload["email"],
                    "username": payload["username"],
                    "password_hash": payload["password_hash"],
                    "role": "owner" if payload["email"] == OWNER_EMAIL else "user",
                    "is_owner": payload["email"] == OWNER_EMAIL,
                    "status": "active",
                    "email_verified": True,
                    "created_at": utc_now_naive(),
                    "updated_at": utc_now_naive(),
                })
            except Exception:
                flash("That email is already registered or MongoDB rejected the account.", "danger")
                return redirect(url_for("signup"))
            finish_login({"email": payload["email"], "username": payload["username"], "is_owner": payload["email"] == OWNER_EMAIL, "role": "owner" if payload["email"] == OWNER_EMAIL else "user"})
            record_audit("signup", payload["email"])
            flash("Account created and verified.", "success")
            return redirect(url_for("admin_dashboard" if session["user"].get("is_owner") else "account"))

        email = request.form.get("email", "").strip().lower()
        username = request.form.get("username", "").strip() or email.split("@")[0]
        password = request.form.get("password", "")
        if not email or "@" not in email or not password or len(password) < 8:
            flash("Use a valid email and a password with at least 8 characters.", "danger")
            return render_template("signup.html", active_page="signup")
        if not using_mongo():
            flash("MongoDB is not configured yet, so signup is disabled. Use the owner login from your .env file.", "warning")
            return redirect(url_for("login"))
        if users_col.find_one({"email": email}):
            flash("That email is already registered.", "danger")
            return render_template("signup.html", active_page="signup")
        payload = {"email": email, "username": username, "password_hash": generate_password_hash(password)}
        if REQUIRE_EMAIL_CODES:
            if not start_email_code_flow("signup", payload, email, "signup"):
                flash("Email verification is not configured. Add RESEND_API_KEY in .env. Emails will send from @moealturej.com with Gmail as reply-to.", "danger")
                return render_template("signup.html", active_page="signup")
            flash("Check your email for the 6-digit signup code.", "success")
            return render_template("verify_email.html", flow="signup", email=email, active_page="signup")
        users_col.insert_one({**payload, "password_hash": payload["password_hash"], "role": "user", "is_owner": email == OWNER_EMAIL, "status": "active", "email_verified": False, "created_at": utc_now_naive(), "updated_at": utc_now_naive()})
        finish_login({"email": email, "username": username, "is_owner": email == OWNER_EMAIL, "role": "owner" if email == OWNER_EMAIL else "user"})
        flash("Account created.", "success")
        return redirect(url_for("admin_dashboard" if email == OWNER_EMAIL else "account"))
    return render_template("signup.html", active_page="signup")

@app.route("/login", methods=["GET", "POST"])
@limiter.limit("12 per minute")
def login():
    if request.method == "POST":
        code = request.form.get("code", "").strip()
        if code and session.get("pending_login"):
            payload = consume_email_code_flow("login", code)
            if not payload:
                flash("Invalid or expired security code.", "danger")
                return render_template("verify_email.html", flow="login", email=session.get("pending_login", {}).get("email"), active_page="login")
            finish_login(payload)
            record_audit("login", payload.get("email", ""))
            flash("Logged in securely.", "success")
            return redirect(request.args.get("next") or url_for("admin_dashboard" if session["user"].get("is_owner") else "account"))

        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = None
        if using_mongo():
            user = users_col.find_one({"email": email})
            valid = bool(user and check_password_hash(user.get("password_hash", ""), password))
            if valid and user.get("status") == "suspended":
                flash("This account is suspended.", "danger")
                return render_template("login.html", active_page="login")
        else:
            valid = email == OWNER_EMAIL and password == OWNER_PASSWORD
            if valid:
                user = {"email": OWNER_EMAIL, "username": OWNER_USERNAME, "is_owner": True, "role": "owner", "status": "active"}
        if not valid:
            flash("Invalid email or password.", "danger")
            return render_template("login.html", active_page="login")
        payload = {"email": user.get("email"), "username": user.get("username") or email.split("@")[0], "is_owner": bool(user.get("is_owner") or user.get("role") == "owner"), "role": user.get("role", "user")}
        if REQUIRE_EMAIL_CODES:
            if not start_email_code_flow("login", payload, email, "login"):
                flash("Email 2FA is not configured. Add RESEND_API_KEY in .env. Emails will send from @moealturej.com with Gmail as reply-to.", "danger")
                return render_template("login.html", active_page="login")
            flash("Check your email for the 6-digit login code.", "success")
            return render_template("verify_email.html", flow="login", email=email, active_page="login")
        finish_login(payload)
        flash("Logged in successfully.", "success")
        return redirect(request.args.get("next") or url_for("admin_dashboard" if session["user"].get("is_owner") else "account"))
    return render_template("login.html", active_page="login")

@app.route("/auth/discord")
@limiter.limit("8 per minute")
def discord_login():
    if not discord_oauth_ready():
        flash("Discord login is missing OAuth keys: " + ", ".join(discord_oauth_missing()) + ". A bot token alone is not enough for website login.", "warning")
        return redirect(url_for("login"))
    state = secrets.token_urlsafe(24)
    session["discord_oauth_state"] = state
    next_url = request.args.get("next") or request.referrer or url_for("account")
    if next_url.startswith(request.host_url):
        next_url = next_url.replace(request.host_url.rstrip("/"), "", 1)
    if not str(next_url).startswith("/"):
        next_url = url_for("account")
    session["discord_oauth_next"] = next_url
    params = {
        "client_id": DISCORD_CLIENT_ID,
        "redirect_uri": discord_redirect_uri(),
        "response_type": "code",
        "scope": discord_oauth_scopes(),
        "state": state,
        "prompt": "consent" if discord_guild_join_ready() else "none",
    }
    return redirect(f"{DISCORD_API_BASE}/oauth2/authorize?{urlencode(params)}")


@app.route("/auth/discord/callback")
@limiter.limit("8 per minute")
def discord_callback():
    if not discord_oauth_ready():
        flash("Discord login is not configured yet.", "warning")
        return redirect(url_for("login"))
    if request.args.get("error"):
        flash("Discord login was cancelled or denied.", "warning")
        return redirect(url_for("login"))
    state = request.args.get("state", "")
    if not state or not secrets.compare_digest(state, session.get("discord_oauth_state", "")):
        flash("Discord login session expired. Try again.", "danger")
        return redirect(url_for("login"))
    code = request.args.get("code", "")
    if not code:
        flash("Discord did not return a login code.", "danger")
        return redirect(url_for("login"))
    try:
        token_resp = requests.post(
            f"{DISCORD_API_BASE}/oauth2/token",
            data={
                "client_id": DISCORD_CLIENT_ID,
                "client_secret": DISCORD_CLIENT_SECRET,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": discord_redirect_uri(),
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=12,
        )
        token_resp.raise_for_status()
        token = token_resp.json().get("access_token")
        if not token:
            raise RuntimeError("Discord did not return an access token")
        user_resp = requests.get(f"{DISCORD_API_BASE}/users/@me", headers={"Authorization": f"Bearer {token}"}, timeout=12)
        user_resp.raise_for_status()
        profile = user_resp.json()
        join_ok, join_msg = add_discord_user_to_guild(str(profile.get("id") or ""), token) if discord_guild_join_ready() else (True, "")
        if not join_ok:
            logger.warning("Discord guild join warning: %s", join_msg)
            flash(join_msg, "warning")
    except Exception as exc:
        logger.error("Discord OAuth failed: %s", exc)
        flash("Discord login failed. Try again in a minute.", "danger")
        return redirect(url_for("login"))
    user = upsert_discord_user(profile)
    if not user:
        flash("Discord login needs MongoDB for normal accounts. Configure MongoDB first, or log in with the owner email/password.", "warning")
        return redirect(url_for("login"))
    if user.get("status") == "suspended":
        flash("This account is suspended.", "danger")
        return redirect(url_for("login"))
    finish_login(user | {"auth_provider": "discord"})
    record_audit("discord_login", user.get("email", user.get("discord_id", "")))
    flash("Logged in with Discord.", "success")
    next_url = session.pop("discord_oauth_next", None) or url_for("admin_dashboard" if session["user"].get("is_owner") else "account")
    session.pop("discord_oauth_state", None)
    return redirect(next_url)



@app.route("/auth/google")
@limiter.limit("8 per minute")
def google_login():
    if not google_oauth_ready():
        flash("Google login is missing OAuth keys: " + ", ".join(google_oauth_missing()) + ".", "warning")
        return redirect(url_for("login"))
    state = secrets.token_urlsafe(24)
    session["google_oauth_state"] = state
    next_url = request.args.get("next") or request.referrer or url_for("account")
    if next_url.startswith(request.host_url):
        next_url = next_url.replace(request.host_url.rstrip("/"), "", 1)
    if not str(next_url).startswith("/"):
        next_url = url_for("account")
    session["google_oauth_next"] = next_url
    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": google_redirect_uri(),
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "access_type": "online",
        "prompt": "select_account",
    }
    return redirect(f"{GOOGLE_AUTH_URL}?{urlencode(params)}")


@app.route("/auth/google/callback")
@limiter.limit("8 per minute")
def google_callback():
    if not google_oauth_ready():
        flash("Google login is not configured yet.", "warning")
        return redirect(url_for("login"))
    if request.args.get("error"):
        flash("Google login was cancelled or denied.", "warning")
        return redirect(url_for("login"))
    state = request.args.get("state", "")
    if not state or not secrets.compare_digest(state, session.get("google_oauth_state", "")):
        flash("Google login session expired. Try again.", "danger")
        return redirect(url_for("login"))
    code = request.args.get("code", "")
    if not code:
        flash("Google did not return a login code.", "danger")
        return redirect(url_for("login"))
    try:
        token_resp = requests.post(
            GOOGLE_TOKEN_URL,
            data={
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": google_redirect_uri(),
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=12,
        )
        token_resp.raise_for_status()
        token = token_resp.json().get("access_token")
        if not token:
            raise RuntimeError("Google did not return an access token")
        profile_resp = requests.get(GOOGLE_USERINFO_URL, headers={"Authorization": f"Bearer {token}"}, timeout=12)
        profile_resp.raise_for_status()
        profile = profile_resp.json()
    except Exception as exc:
        logger.error("Google OAuth failed: %s", exc)
        flash("Google login failed. Try again in a minute.", "danger")
        return redirect(url_for("login"))
    user = upsert_google_user(profile)
    if not user:
        flash("Google login needs MongoDB for customer accounts. Configure MongoDB first, or log in with the owner email/password.", "warning")
        return redirect(url_for("login"))
    if user.get("status") == "suspended":
        flash("This account is suspended.", "danger")
        return redirect(url_for("login"))
    finish_login(user | {"auth_provider": "google"})
    record_audit("google_login", user.get("email", user.get("google_id", "")))
    flash("Logged in with Google.", "success")
    next_url = session.pop("google_oauth_next", None) or url_for("admin_dashboard" if session["user"].get("is_owner") else "account")
    session.pop("google_oauth_state", None)
    return redirect(next_url)


@app.route("/forgot-password", methods=["GET", "POST"])
@limiter.limit("8 per minute")
def forgot_password():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        generic = "If that email exists, a secure reset link has been sent."
        if not using_mongo():
            flash("Password reset needs MongoDB connected.", "warning")
            return render_template("forgot_password.html", active_page="login")
        if not email or "@" not in email:
            flash(generic, "success")
            return render_template("forgot_password.html", active_page="login")
        user = users_col.find_one({"email": email})
        if user and user.get("password_hash") is not None:
            token = secrets.token_urlsafe(38)
            users_col.update_one({"_id": user["_id"]}, {"$set": {
                "password_reset_hash": generate_password_hash(token),
                "password_reset_expires": utc_now_naive() + timedelta(minutes=20),
                "updated_at": utc_now_naive(),
            }})
            reset_url = url_for("reset_password", token=token, _external=True)
            if not send_password_reset_email(email, reset_url):
                flash("Password reset email is not configured. Add RESEND_API_KEY in .env for password reset emails.", "danger")
                return render_template("forgot_password.html", active_page="login")
            record_audit("password_reset_requested", email)
        flash(generic, "success")
        return redirect(url_for("login"))
    return render_template("forgot_password.html", active_page="login")


@app.route("/reset-password/<token>", methods=["GET", "POST"])
@limiter.limit("8 per minute")
def reset_password(token):
    user = find_user_by_reset_token(token)
    if not user:
        flash("That password reset link is invalid or expired.", "danger")
        return redirect(url_for("forgot_password"))
    if request.method == "POST":
        password = request.form.get("password", "")
        confirm = request.form.get("confirm", "")
        if len(password) < 8 or password != confirm:
            flash("Use matching passwords with at least 8 characters.", "danger")
            return render_template("reset_password.html", token=token, active_page="login")
        users_col.update_one({"_id": user["_id"]}, {"$set": {
            "password_hash": generate_password_hash(password),
            "updated_at": utc_now_naive(),
        }, "$unset": {"password_reset_hash": "", "password_reset_expires": ""}})
        record_audit("password_reset_completed", user.get("email", ""))
        flash("Password updated. You can sign in now.", "success")
        return redirect(url_for("login"))
    return render_template("reset_password.html", token=token, active_page="login")


@app.route("/logout")
def logout():
    session.clear()
    flash("Logged out.", "success")
    return redirect(url_for("home"))

@app.route("/account")
def account():
    cleanup_expired_pending_orders()
    if not current_user():
        return redirect(url_for("login", next=request.path))
    orders = load_orders_for_user((current_user() or {}).get("email"))
    tickets = load_support_tickets_for_user((current_user() or {}).get("email"))
    return render_template("account.html", active_page="account", orders=orders, tickets=tickets, guides=visible_guides())



@app.route("/guides")
def guides():
    if not current_user():
        return redirect(url_for("login", next=request.path))
    items = visible_guides()
    categories = []
    for item in items:
        if item.get("category") not in categories:
            categories.append(item.get("category"))
    return render_template("guides.html", active_page="guides", guides=items, categories=categories)


@app.route("/guides/<slug>")
def guide_detail(slug):
    if not current_user():
        return redirect(url_for("login", next=request.path))
    guide = find_guide(slug)
    if not guide:
        abort(404)
    items = visible_guides()
    return render_template("guide_detail.html", active_page="guides", guide=guide, guide_steps=guide_steps(guide), guides=items)


@app.route("/admin/guides", methods=["POST"])
@owner_required
@limiter.limit("20 per minute")
def admin_guide_new():
    guides_data = get_guides_settings()
    title = (request.form.get("title") or "").strip()
    if not title:
        flash("Guide title is required.", "danger")
        return redirect(url_for("admin_dashboard") + "#guides")
    guide = normalize_guide({
        "id": secrets.token_hex(6),
        "title": title,
        "slug": request.form.get("slug") or title,
        "summary": request.form.get("summary"),
        "category": request.form.get("category"),
        "icon": request.form.get("icon"),
        "badge": request.form.get("badge"),
        "sort_order": request.form.get("sort_order", 100),
        "body": request.form.get("body"),
        "enabled": bool(request.form.get("enabled")),
    })
    existing = {item.get("slug") for item in guides_data.get("items") or []}
    if guide["slug"] in existing:
        guide["slug"] = f'{guide["slug"]}-{secrets.token_hex(2)}'
    guides_data.setdefault("items", []).append(guide)
    save_guides_settings(guides_data)
    record_audit("guide_created", guide["slug"])
    flash("Guide created.", "success")
    return redirect(url_for("admin_dashboard") + "#guides")


@app.route("/admin/guides/<guide_id>", methods=["POST"])
@owner_required
@limiter.limit("30 per minute")
def admin_guide_update(guide_id):
    guides_data = get_guides_settings()
    items = guides_data.get("items") or []
    target = next((item for item in items if str(item.get("id")) == str(guide_id)), None)
    if not target:
        flash("Guide not found.", "danger")
        return redirect(url_for("admin_dashboard") + "#guides")
    target.update(normalize_guide({
        **target,
        "title": request.form.get("title") or target.get("title"),
        "slug": request.form.get("slug") or target.get("slug"),
        "summary": request.form.get("summary"),
        "category": request.form.get("category"),
        "icon": request.form.get("icon"),
        "badge": request.form.get("badge"),
        "sort_order": request.form.get("sort_order", target.get("sort_order", 100)),
        "body": request.form.get("body"),
        "enabled": bool(request.form.get("enabled")),
        "updated_at": utc_now().isoformat(),
    }))
    save_guides_settings(guides_data)
    record_audit("guide_updated", target.get("slug", guide_id))
    flash("Guide updated.", "success")
    return redirect(url_for("admin_dashboard") + "#guides")


@app.route("/admin/guides/<guide_id>/delete", methods=["POST"])
@owner_required
@limiter.limit("15 per minute")
def admin_guide_delete(guide_id):
    guides_data = get_guides_settings()
    before = len(guides_data.get("items") or [])
    guides_data["items"] = [item for item in guides_data.get("items") or [] if str(item.get("id")) != str(guide_id)]
    if len(guides_data["items"]) == before:
        flash("Guide not found.", "danger")
    else:
        save_guides_settings(guides_data)
        record_audit("guide_deleted", str(guide_id))
        flash("Guide deleted.", "success")
    return redirect(url_for("admin_dashboard") + "#guides")


@app.route("/owner")
def build_email_preview(kind: str) -> tuple[str, str]:
    """Create sample email HTML without sending anything."""
    kind = (kind or "security").strip().lower()
    if kind == "security":
        subject, _, html = build_security_email("482193", "sign in")
    elif kind == "password":
        body = """<div style='padding:18px;border-radius:14px;background:#0b0811;border:1px solid #2d2240;'><div style='font-size:14px;font-weight:800;color:#fff;'>Password reset requested</div><p style='margin:8px 0 0;color:#9f94aa;font-size:13px;line-height:1.65;'>Use the button below to create a new password. This secure link expires in 20 minutes.</p></div>"""
        subject = "Reset your password"
        html = branded_email_shell(subject, "Password recovery", "Use this secure link to reset your password for your moealturej account.", body, "Reset password", f"{APP_URL}/reset-password/demo")
    elif kind == "key":
        sample_order = {"order_id": "MOE-DEMO-1842"}
        sample_item = {"product_name": "Example Product", "option_name": "30 Days"}
        subject, _, html = build_key_delivery_email("customer@example.com", sample_order, sample_item, "DEMO-8F2K-4L9P-X7QW", "Keep this key private and follow the guide in your account.")
    elif kind == "support":
        body = """<div style='padding:18px;border-radius:14px;background:#0b0811;border:1px solid #2d2240;'><div style='font-size:13px;color:#8f839a;'>Ticket #SUP-1842</div><div style='margin-top:8px;font-size:15px;font-weight:800;color:#fff;'>Installation help</div><p style='margin:10px 0 0;color:#9f94aa;font-size:13px;line-height:1.65;'>We replied to your ticket. Open it to view the full response and continue the conversation.</p></div>"""
        subject = "Support reply received"
        html = branded_email_shell(subject, "Support ticket", "Your support conversation was updated.", body, "Open ticket", f"{APP_URL}/support")
    else:
        subject, _, html = build_admin_broadcast_email("Store update", "A new product update is available now.\n\nSign in to view the latest details and downloads.", "View update", f"{APP_URL}/store")
    return subject, html


@app.route("/admin/email-previews")
@owner_required
def admin_email_previews():
    preview_types = [
        ("security", "Security code"),
        ("password", "Password reset"),
        ("key", "Product key"),
        ("support", "Support reply"),
        ("broadcast", "Store update"),
    ]
    return render_template("email_previews.html", preview_types=preview_types, active_page="admin")


@app.route("/admin/email-previews/<kind>")
@owner_required
def admin_email_preview_document(kind):
    _, html = build_email_preview(kind)
    response = Response(html, mimetype="text/html")
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response


@app.route("/admin")
@owner_required
def admin_dashboard():
    cleanup_expired_pending_orders()
    products = load_products()
    stats = {
        "products": len(products),
        "store": len([p for p in products if is_store_product(p)]),
        "downloads": len([p for p in products if is_download_product(p)]),
        "status": len([p for p in products if is_status_product(p)]),
        "storage": "MongoDB" if using_mongo() else "local JSON fallback",
        "users": len(load_admin_users()),
        "media": len(load_media()),
        "orders": len(load_orders_for_user(None)),
        "support_tickets": len(load_support_tickets()),
        "applications": len(visible_applications(include_disabled=True)),
        "guides": len(visible_guides(include_disabled=True)),
        "application_submissions": len((get_applications_settings().get("submissions") or [])),
        "payments": ("Stripe " if STRIPE_SECRET_KEY else "") + ("PayPal" if PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET else "") or "not configured",
        "owner_webhook": "configured" if OWNER_ORDER_WEBHOOK_URL else "missing",
        "auto_delivery": "ready" if (RESELLING_PRO_ENABLED and RESELLING_PRO_API_KEY) else ("disabled" if not RESELLING_PRO_ENABLED else "missing API key"),
    }
    return render_template("admin.html", products=products, users=load_admin_users(), media_items=load_media(), orders=load_orders_for_user(None), support_tickets=load_support_tickets(), applications_settings=get_applications_settings(), guides_settings=get_guides_settings(), stats=stats, site_settings=load_site_settings(), mongo_status_reason=mongo_status_reason, active_page="admin")


@app.route("/admin/settings", methods=["POST"])
@owner_required
def admin_site_settings():
    current = load_site_settings()
    def clean_link(field: str, fallback: str = "") -> str:
        value = (request.form.get(field) or fallback).strip()[:500]
        if not value:
            return fallback
        if value.startswith("/") and not value.startswith("//"):
            return value
        parsed = urlparse(value)
        if parsed.scheme in {"http", "https"} and parsed.netloc:
            return value
        return fallback

    support_email = (request.form.get("support_email") or SUPPORT_EMAIL).strip().lower()[:254]
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", support_email):
        support_email = SUPPORT_EMAIL

    current.update({
        "maintenance_enabled": bool(request.form.get("maintenance_enabled")),
        "store_enabled": bool(request.form.get("store_enabled")),
        "registration_enabled": bool(request.form.get("registration_enabled")),
        "announcement_enabled": bool(request.form.get("announcement_enabled")),
        "announcement_text": request.form.get("announcement_text", "").strip()[:300],
        "announcement_type": request.form.get("announcement_type", "info").strip()[:20],
        "site_name": (request.form.get("site_name") or APP_NAME).strip()[:80] or APP_NAME,
        "site_tagline": (request.form.get("site_tagline") or "").strip()[:180],
        "site_url": clean_link("site_url", APP_URL),
        "support_email": support_email,
        "support_url": clean_link("support_url", "/support"),
        "discord_url": clean_link("discord_url", ""),
        "store_url": clean_link("store_url", "/store"),
        "downloads_url": clean_link("downloads_url", "/downloads"),
        "status_url": clean_link("status_url", "/status"),
        "applications_url": clean_link("applications_url", "/applications"),
        "legal_url": clean_link("legal_url", "/legal"),
    })
    save_site_settings(current)
    record_audit("site_settings_update", "site", current)
    flash("Site settings updated.", "success")
    return redirect(url_for("admin_dashboard") + "#owner")


@app.route("/admin/email/send", methods=["POST"])
@owner_required
def admin_custom_email_send():
    if not verify_csrf():
        abort(400)
    try:
        subject = (request.form.get("email_subject") or "").strip()
        message = (request.form.get("email_message") or "").strip()
        cta_label = (request.form.get("email_cta_label") or "Open moealturej").strip()[:60]
        cta_url = (request.form.get("email_cta_url") or APP_URL).strip() or APP_URL
        if not (cta_url.startswith("https://") or cta_url.startswith("http://")):
            cta_url = APP_URL

        if not subject or not message:
            flash("Add a subject and message before sending.", "danger")
            return redirect(url_for("admin_dashboard") + "#emails")

        recipients = parse_admin_email_recipients()
        if not recipients:
            flash("Choose at least one recipient.", "danger")
            return redirect(url_for("admin_dashboard") + "#emails")

        built_subject, text, html_body = build_admin_broadcast_email(subject, message, cta_label, cta_url)
        sent = 0
        failed = 0
        # Use the same verified sender group as OTP/security emails to avoid no-reply sender issues on Resend.
        for email in recipients[:500]:
            try:
                ok = send_html_email(email, built_subject, text, html_body, "Security")
            except Exception:
                logger.exception("Admin email send crashed for recipient %s", email)
                ok = False
            if ok:
                sent += 1
            else:
                failed += 1
        record_audit("email.broadcast", "admin", {"subject": built_subject, "sent": sent, "failed": failed, "requested": len(recipients)})
        if failed:
            flash(f"Custom email sent to {sent} recipient(s). {failed} failed. Check Render logs or Resend if delivery failed.", "warning")
        else:
            flash(f"Custom email sent to {sent} recipient(s).", "success")
        return redirect(url_for("admin_dashboard") + "#emails")
    except Exception:
        logger.exception("Admin custom email sender failed")
        flash("The email sender hit a server error. Nothing else on the site was changed. Check Render logs for the exact Resend/Mongo error.", "danger")
        return redirect(url_for("admin_dashboard") + "#emails")


@app.route("/admin/discounts", methods=["POST"])
@owner_required
def admin_discounts_update():
    settings = load_site_settings()
    codes = []
    seen = set()
    for raw in request.form.get("codes_text", "").splitlines():
        parts = [p.strip() for p in raw.split("|")]
        if not parts or not clean_discount_code(parts[0]):
            continue
        code = clean_discount_code(parts[0])
        if code in seen:
            continue
        seen.add(code)
        dtype = normalize_discount_type(parts[1] if len(parts) > 1 else "percent")
        try:
            value = max(0, float(parts[2] if len(parts) > 2 else 0))
        except Exception:
            value = 0
        label = (parts[3] if len(parts) > 3 and parts[3] else f"Code {code}")[:80]
        try:
            min_subtotal = max(0, float(parts[4] if len(parts) > 4 and parts[4] else 0))
        except Exception:
            min_subtotal = 0
        try:
            max_uses = max(0, int(parts[5] if len(parts) > 5 and parts[5] else 0))
        except Exception:
            max_uses = 0
        expires_at = (parts[6] if len(parts) > 6 else "").strip()[:40]
        old = next((c for c in ((settings.get("discounts") or {}).get("codes") or []) if clean_discount_code(c.get("code")) == code), {})
        codes.append({"enabled": True, "code": code, "type": dtype, "value": value, "label": label, "min_subtotal": min_subtotal, "max_uses": max_uses, "used_count": int(old.get("used_count") or 0), "expires_at": expires_at})

    def pack(prefix: str, default_label: str, extra: dict | None = None):
        d = {
            "enabled": bool(request.form.get(f"{prefix}_enabled")),
            "label": request.form.get(f"{prefix}_label", default_label).strip()[:80] or default_label,
            "type": normalize_discount_type(request.form.get(f"{prefix}_type")),
            "value": max(0, float(request.form.get(f"{prefix}_value") or 0)),
            "min_subtotal": max(0, float(request.form.get(f"{prefix}_min_subtotal") or 0)),
        }
        if extra:
            d.update(extra)
        return d

    try:
        discounts = {
            "enabled": bool(request.form.get("discounts_enabled")),
            "store_auto": pack("store_auto", "Store discount"),
            "bulk": pack("bulk", "Bulk discount", {"min_quantity": max(1, int(request.form.get("bulk_min_quantity") or 2))}),
            "discord": pack("discord", "Discord linked discount"),
            "codes": codes,
        }
    except Exception as exc:
        flash(f"Discount settings were invalid: {exc}", "danger")
        return redirect(url_for("admin_dashboard") + "#discounts")
    settings["discounts"] = discounts
    save_site_settings(settings)
    record_audit("discounts_update", "site", {"code_count": len(codes)})
    flash("Discount settings updated.", "success")
    return redirect(url_for("admin_dashboard") + "#discounts")


@app.route("/admin/product/<slug>/discount", methods=["POST"])
@owner_required
def admin_product_discount_update(slug):
    products = load_products()
    product = next((p for p in products if str(p.get("slug")) == slug), None)
    if not product:
        abort(404)
    store = product.setdefault("store", {})
    try:
        value = max(0, float(request.form.get("product_discount_value") or 0))
        discount = {
            "enabled": bool(request.form.get("product_discount_enabled")),
            "label": request.form.get("product_discount_label", "Product discount").strip()[:80] or "Product discount",
            "type": normalize_discount_type(request.form.get("product_discount_type")),
            "value": value,
            "min_subtotal": max(0, float(request.form.get("product_discount_min_subtotal") or 0)),
            "min_quantity": max(1, int(request.form.get("product_discount_min_quantity") or 1)),
        }
    except Exception as exc:
        flash(f"Product discount settings were invalid: {exc}", "danger")
        return redirect(url_for("admin_dashboard") + "#products")
    store["autoDiscount"] = discount
    product = normalize_product(product)
    if using_mongo():
        products_col.update_one({"slug": product["slug"]}, {"$set": {"store": product["store"]}})
    else:
        save_products_file(products)
    record_audit("product_discount_update", product.get("slug", slug), discount)
    flash("Product discount updated.", "success")
    return redirect(url_for("admin_dashboard") + "#products")

@app.route("/admin/applications", methods=["POST"])
@owner_required
def admin_application_new():
    apps = get_applications_settings()
    questions = parse_application_questions_text(request.form.get("questions_text", ""))
    app_data = normalize_application({
        "id": secrets.token_hex(8),
        "title": request.form.get("title", "New Application"),
        "slug": request.form.get("slug") or request.form.get("title", "New Application"),
        "enabled": bool(request.form.get("enabled")),
        "featured": bool(request.form.get("featured")),
        "badge": request.form.get("badge", "Open"),
        "image": request.form.get("image", ""),
        "short_description": request.form.get("short_description", ""),
        "description": request.form.get("description", ""),
        "instructions": request.form.get("instructions", ""),
        "webhook_url": request.form.get("webhook_url", ""),
        "success_message": request.form.get("success_message", "Your application was submitted successfully."),
        "questions": questions,
    })
    if app_data.get("webhook_url") and not discord_webhook_allowed(app_data.get("webhook_url")):
        flash("Application webhook must be a Discord webhook URL.", "danger")
        return redirect(url_for("admin_dashboard") + "#applications")
    existing_slugs = {a.get("slug") for a in apps.get("items") or []}
    if app_data.get("slug") in existing_slugs:
        app_data["slug"] = f"{app_data.get('slug')}-{secrets.token_hex(2)}"
    apps["items"] = list(apps.get("items") or []) + [app_data]
    save_applications_settings(apps)
    record_audit("application_created", app_data.get("slug", ""))
    flash("Application created.", "success")
    return redirect(url_for("admin_dashboard") + "#applications")


@app.route("/admin/applications/<app_id>", methods=["POST"])
@owner_required
def admin_application_update(app_id):
    apps = get_applications_settings()
    items = list(apps.get("items") or [])
    current = next((a for a in items if a.get("id") == app_id or a.get("slug") == app_id), None)
    if not current:
        abort(404)
    questions = parse_application_questions_text(request.form.get("questions_text", ""))
    updated = normalize_application({
        **current,
        "title": request.form.get("title", current.get("title")),
        "slug": request.form.get("slug", current.get("slug")),
        "enabled": bool(request.form.get("enabled")),
        "featured": bool(request.form.get("featured")),
        "badge": request.form.get("badge", current.get("badge")),
        "image": request.form.get("image", current.get("image")),
        "short_description": request.form.get("short_description", current.get("short_description")),
        "description": request.form.get("description", current.get("description")),
        "instructions": request.form.get("instructions", current.get("instructions")),
        "webhook_url": request.form.get("webhook_url", current.get("webhook_url")),
        "success_message": request.form.get("success_message", current.get("success_message")),
        "questions": questions,
        "updated_at": utc_now().isoformat(),
    })
    if updated.get("webhook_url") and not discord_webhook_allowed(updated.get("webhook_url")):
        flash("Application webhook must be a Discord webhook URL.", "danger")
        return redirect(url_for("admin_dashboard") + "#applications")
    apps["items"] = [updated if (a.get("id") == app_id or a.get("slug") == app_id) else a for a in items]
    save_applications_settings(apps)
    record_audit("application_updated", updated.get("slug", ""))
    flash("Application updated.", "success")
    return redirect(url_for("admin_dashboard") + "#applications")


@app.route("/admin/applications/<app_id>/delete", methods=["POST"])
@owner_required
def admin_application_delete(app_id):
    apps = get_applications_settings()
    before = len(apps.get("items") or [])
    apps["items"] = [a for a in (apps.get("items") or []) if a.get("id") != app_id and a.get("slug") != app_id]
    save_applications_settings(apps)
    record_audit("application_deleted", app_id, {"removed": before - len(apps.get("items") or [])})
    flash("Application deleted.", "success")
    return redirect(url_for("admin_dashboard") + "#applications")



@app.route("/admin/products/reorder", methods=["POST"])
@owner_required
def admin_products_reorder():
    raw_order = request.form.get("product_order", "").strip()
    slugs = [clean_slug(item) for item in raw_order.split(",") if clean_slug(item)]
    if not slugs:
        flash("No product order was submitted.", "warning")
        return redirect(url_for("admin_dashboard") + "#products")

    products = load_products()
    product_map = {str(p.get("slug", "")).lower(): p for p in products}
    missing = [slug for slug in slugs if slug not in product_map]
    if missing:
        flash("Some products could not be found, so the order was not saved.", "danger")
        return redirect(url_for("admin_dashboard") + "#products")

    touched = set()
    for index, slug in enumerate(slugs):
        product = product_map[slug]
        product["displayOrder"] = index
        touched.add(slug)

    next_index = len(slugs)
    for product in products:
        slug = str(product.get("slug", "")).lower()
        if slug not in touched:
            product["displayOrder"] = next_index
            next_index += 1

    if using_mongo():
        for product in products:
            products_col.update_one({"slug": product["slug"]}, {"$set": {"displayOrder": int(product.get("displayOrder", 9999))}})
    else:
        save_products_file(sorted(products, key=lambda p: (int(p.get("displayOrder", 9999)), str(p.get("name", "")).lower())))

    record_audit("products.reordered", "products", {"count": len(slugs), "order": slugs})
    flash("Product order saved.", "success")
    return redirect(url_for("admin_dashboard") + "#products")

@app.route("/admin/product/new", methods=["POST"])
@owner_required
def admin_product_new():
    name = request.form.get("name", "New Product").strip() or "New Product"
    slug = clean_slug(request.form.get("slug") or name)
    short_description = request.form.get("description", "").strip()
    parsed_details = parse_detailed_description(request.form.get("detailed_source", ""))
    detailed_description = parsed_details or short_description
    try:
        store_options = build_store_options_from_form()
    except ValueError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("admin_dashboard") + "#create")
    product = normalize_product({
        "id": int(utc_now().timestamp() * 1000),
        "slug": slug,
        "name": name,
        "description": short_description,
        "detailedDescription": detailed_description,
        "image": request.form.get("image", "/static/logo.png"),
        "category": request.form.get("category", "general"),
        "type": "product",
        "displayOrder": len(load_products()),
        "featured": bool(request.form.get("featured")),
        "features": [],
        "store": {"enabled": bool(request.form.get("store_enabled")), "stockStatus": "In Stock", "options": store_options},
        "downloads": {"enabled": bool(request.form.get("downloads_enabled")), "version": "Latest", "downloadUrl": request.form.get("download_url", ""), "fileSize": request.form.get("file_size", "")},
        "status": {"enabled": bool(request.form.get("status_enabled")), "state": "Operational", "label": "Online", "lastUpdated": today_utc_date()},
    })
    if using_mongo():
        products_col.update_one({"slug": product["slug"]}, {"$set": product}, upsert=True)
    else:
        products = [p for p in load_products() if p.get("slug") != product["slug"]]
        products.append(product)
        save_products_file(products)
    flash("Product added.", "success")
    return redirect(url_for("admin_dashboard"))

@app.route("/admin/product/<slug>", methods=["POST"])
@owner_required
def admin_product_update(slug):
    raw = request.form.get("product_json", "").strip()
    try:
        product = normalize_product(json.loads(raw))
    except Exception as exc:
        flash(f"Invalid JSON: {exc}", "danger")
        return redirect(url_for("admin_dashboard"))
    old_slug = slug.strip().lower()
    if using_mongo():
        products_col.delete_one({"slug": old_slug})
        products_col.update_one({"slug": product["slug"]}, {"$set": product}, upsert=True)
    else:
        products = [p for p in load_products() if str(p.get("slug", "")).lower() != old_slug]
        products.append(product)
        save_products_file(products)
    flash("Product updated.", "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/product/<slug>/download", methods=["POST"])
@owner_required
@limiter.limit("20 per minute")
def admin_product_download_update(slug):
    products = load_products()
    product = next((p for p in products if str(p.get("slug", "")).lower() == slug.lower()), None)
    if not product:
        abort(404)

    downloads = product.setdefault("downloads", {}) if isinstance(product.get("downloads"), dict) else {}
    product["downloads"] = downloads
    downloads["enabled"] = bool(request.form.get("downloads_enabled"))
    downloads["version"] = (request.form.get("download_version") or downloads.get("version") or "Latest").strip()

    manual_url = (request.form.get("download_url") or "").strip()
    manual_size = (request.form.get("file_size") or "").strip()
    upload = request.files.get("download_file")

    if upload and upload.filename:
        if not allowed_upload(upload.filename, "file"):
            flash("That download file type is not allowed.", "danger")
            return redirect(url_for("admin_dashboard") + "#products")
        original = secure_filename(upload.filename)
        ext = original.rsplit(".", 1)[-1].lower() if "." in original else "bin"
        filename = f"{utc_now_naive().strftime('%Y%m%d')}-{uuid.uuid4().hex[:12]}.{ext}"
        FILES_DIR.mkdir(parents=True, exist_ok=True)
        local_path = FILES_DIR / filename
        upload.save(local_path)
        url = url_for("download_file", filename=filename)
        mime_type = mimetypes.guess_type(original)[0] or "application/octet-stream"
        gridfs_id = save_upload_to_mongo_storage(local_path, filename, original, "file", mime_type)
        downloads["downloadUrl"] = url
        downloads["fileSize"] = manual_size or human_file_size(local_path.stat().st_size)
        save_media_record({
            "filename": filename,
            "original_name": original,
            "kind": "file",
            "url": url,
            "mime_type": mime_type,
            "size_bytes": local_path.stat().st_size,
            "storage": "mongodb_gridfs" if gridfs_id else "local_disk",
            "gridfs_id": gridfs_id,
            "created_at": utc_now().isoformat(),
            "created_by": (current_user() or {}).get("email"),
        })
        record_audit("product_download_file_update", product.get("slug", slug), {"filename": filename})
    else:
        if manual_url:
            downloads["downloadUrl"] = manual_url
        if manual_size:
            downloads["fileSize"] = manual_size

    product = normalize_product(product)
    if using_mongo():
        products_col.update_one({"slug": product["slug"]}, {"$set": {"downloads": product["downloads"]}})
    else:
        save_products_file(products)
    flash("Download details updated.", "success")
    return redirect(url_for("admin_dashboard") + "#products")


@app.route("/admin/product/<slug>/auto-delivery", methods=["POST"])
@owner_required
def admin_product_auto_delivery_update(slug):
    products = load_products()
    product = next((p for p in products if str(p.get("slug", "")).lower() == slug.lower()), None)
    if not product:
        abort(404)

    store = product.setdefault("store", {}) if isinstance(product.get("store"), dict) else {}
    product["store"] = store
    options = store.setdefault("options", []) if isinstance(store.get("options"), list) else []
    store["options"] = options

    product_base_url = request.form.get("product_auto_base_url", "").strip()
    product_enabled = bool(request.form.get("product_auto_enabled"))
    try:
        if product_base_url:
            store["autoDelivery"] = auto_delivery_dict_from_base_url(product_base_url, product_enabled)
        else:
            store.pop("autoDelivery", None)
            store.pop("auto_delivery", None)

        enabled_option_ids = set(request.form.getlist("option_auto_enabled"))
        for option in options:
            option_key = str(option.get("id") or option.get("uniqid") or option.get("name") or "").strip()
            raw_url = request.form.get(f"option_auto_base_url_{option_key}", "").strip()
            enabled = option_key in enabled_option_ids
            if raw_url:
                option["autoDelivery"] = auto_delivery_dict_from_base_url(raw_url, enabled)
            else:
                option.pop("autoDelivery", None)
                option.pop("auto_delivery", None)
    except ValueError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("admin_dashboard") + "#products")

    product = normalize_product(product)
    if using_mongo():
        products_col.update_one({"slug": product["slug"]}, {"$set": {"store": product["store"]}})
    else:
        save_products_file(products)
    record_audit("product_auto_delivery_update", product.get("slug", slug), {"option_count": len(options)})
    flash("Auto-delivery settings updated.", "success")
    return redirect(url_for("admin_dashboard") + "#products")


@app.route("/admin/product/<slug>/delete", methods=["POST"])
@owner_required
def admin_product_delete(slug):
    if using_mongo():
        products_col.delete_one({"slug": slug})
    else:
        save_products_file([p for p in load_products() if str(p.get("slug", "")) != slug])
    flash("Product removed.", "success")
    return redirect(url_for("admin_dashboard"))

@app.route("/admin/status/<slug>", methods=["POST"])
@owner_required
def admin_status_update(slug):
    products = load_products()
    product = next((p for p in products if str(p.get("slug")) == slug), None)
    if not product:
        abort(404)
    product["status"] = {
        "enabled": bool(request.form.get("enabled")),
        "state": request.form.get("state", "Operational"),
        "label": request.form.get("label", "Online"),
        "lastUpdated": today_utc_date(),
    }
    if using_mongo():
        products_col.update_one({"slug": slug}, {"$set": {"status": product["status"]}})
    else:
        save_products_file(products)
    flash("Status updated.", "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/user/<path:email>/role", methods=["POST"])
@owner_required
def admin_user_role(email):
    if not using_mongo():
        flash("MongoDB is required for account control.", "warning")
        return redirect(url_for("admin_dashboard"))
    target = email.strip().lower()
    role = request.form.get("role", "user").strip().lower()
    actor = current_user() or {}
    if role not in {"user", "support", "admin", "owner"}:
        role = "user"
    if target == OWNER_EMAIL and role != "owner":
        flash("The environment owner account must stay owner.", "danger")
        return redirect(url_for("admin_dashboard"))
    if role == "owner" and not actor.get("is_owner"):
        flash("Only the main owner can promote another owner.", "danger")
        return redirect(url_for("admin_dashboard"))
    is_owner = role == "owner"
    users_col.update_one({"email": target}, {"$set": {"role": role, "is_owner": is_owner, "updated_at": utc_now_naive()}})
    record_audit("user_role_update", target, {"role": role})
    flash("User role updated.", "success")
    return redirect(url_for("admin_dashboard"))

@app.route("/admin/user/<path:email>/status", methods=["POST"])
@owner_required
def admin_user_status(email):
    if not using_mongo():
        flash("MongoDB is required for account control.", "warning")
        return redirect(url_for("admin_dashboard"))
    target = email.strip().lower()
    if target == OWNER_EMAIL or target == (current_user() or {}).get("email"):
        flash("You cannot suspend the main owner/current account.", "danger")
        return redirect(url_for("admin_dashboard"))
    status = request.form.get("status", "active")
    users_col.update_one({"email": target}, {"$set": {"status": status, "updated_at": utc_now_naive()}})
    record_audit("user_status_update", target, {"status": status})
    flash("User status updated.", "success")
    return redirect(url_for("admin_dashboard"))

@app.route("/admin/user/<path:email>/password", methods=["POST"])
@owner_required
def admin_user_password(email):
    if not using_mongo():
        flash("MongoDB is required for account control.", "warning")
        return redirect(url_for("admin_dashboard"))
    password = request.form.get("password", "")
    if len(password) < 8:
        flash("Password must be at least 8 characters.", "danger")
        return redirect(url_for("admin_dashboard"))
    target = email.strip().lower()
    users_col.update_one({"email": target}, {"$set": {"password_hash": generate_password_hash(password), "updated_at": utc_now_naive()}})
    record_audit("user_password_reset", target)
    flash("Password reset.", "success")
    return redirect(url_for("admin_dashboard"))

@app.route("/admin/user/<path:email>/delete", methods=["POST"])
@owner_required
def admin_user_delete(email):
    if not using_mongo():
        flash("MongoDB is required for account control.", "warning")
        return redirect(url_for("admin_dashboard"))
    target = email.strip().lower()
    if target == OWNER_EMAIL or target == (current_user() or {}).get("email"):
        flash("You cannot delete the main owner/current account.", "danger")
        return redirect(url_for("admin_dashboard"))
    users_col.delete_one({"email": target})
    record_audit("user_delete", target)
    flash("User deleted.", "success")
    return redirect(url_for("admin_dashboard"))

@app.route("/admin/media/upload", methods=["POST"])
@owner_required
@limiter.limit("20 per minute")
def admin_media_upload():
    upload = request.files.get("upload")
    upload_kind = request.form.get("upload_kind", "image")
    if not upload or not upload.filename:
        flash("Choose a file first.", "danger")
        return redirect(url_for("admin_dashboard"))
    if not allowed_upload(upload.filename, upload_kind):
        flash("That file type is not allowed.", "danger")
        return redirect(url_for("admin_dashboard"))
    original = secure_filename(upload.filename)
    ext = original.rsplit(".", 1)[-1].lower() if "." in original else "bin"
    filename = f"{utc_now_naive().strftime('%Y%m%d')}-{uuid.uuid4().hex[:12]}.{ext}"
    target_dir = UPLOADS_DIR if upload_kind == "image" else FILES_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    local_path = target_dir / filename
    upload.save(local_path)
    url = url_for("media_file", filename=filename) if upload_kind == "image" else url_for("download_file", filename=filename)
    mime_type = mimetypes.guess_type(original)[0] or "application/octet-stream"
    gridfs_id = save_upload_to_mongo_storage(local_path, filename, original, upload_kind, mime_type)
    record = {
        "filename": filename,
        "original_name": original,
        "kind": upload_kind,
        "url": url,
        "mime_type": mime_type,
        "size_bytes": local_path.stat().st_size,
        "storage": "mongodb_gridfs" if gridfs_id else "local_disk",
        "gridfs_id": gridfs_id,
        "created_at": utc_now().isoformat(),
        "created_by": (current_user() or {}).get("email"),
    }
    save_media_record(record)
    record_audit("media_upload", filename, {"kind": upload_kind})
    flash("File uploaded. Copy its URL into a product image or download field.", "success")
    return redirect(url_for("admin_dashboard"))

@app.route("/admin/media/<path:filename>/delete", methods=["POST"])
@owner_required
def admin_media_delete(filename):
    filename = secure_filename(filename)
    for folder in (UPLOADS_DIR, FILES_DIR):
        path = folder / filename
        if path.exists() and path.is_file():
            path.unlink()
    delete_mongo_stored_file(filename)
    delete_media_record(filename)
    record_audit("media_delete", filename)
    flash("Media deleted.", "success")
    return redirect(url_for("admin_dashboard"))

@app.route("/media/<path:filename>")
@limiter.limit("120 per minute")
def media_file(filename):
    safe_name = secure_filename(filename)
    safe_path = safe_join(str(UPLOADS_DIR), safe_name)
    if not safe_path:
        abort(404)
    file_path = Path(safe_path)
    if file_path.exists() and file_path.is_file():
        return send_from_directory(str(UPLOADS_DIR), safe_name, conditional=True)
    return send_mongo_stored_file(safe_name, as_attachment=False)

# -----------------------------------------------------------------------------
# Checkout / Payments
# -----------------------------------------------------------------------------
@app.route("/api/checkout/preview", methods=["POST"])
@limiter.limit("60 per minute")
def api_checkout_preview():
    try:
        payload = request.get_json(silent=True) or {}
        cart = build_checkout_cart(payload.get("items") or [], payload.get("discount_code") or payload.get("code") or "", current_user())
        return jsonify({"ok": True, "cart": cart})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.route("/checkout/stripe", methods=["POST"])
@login_required
@limiter.limit("20 per minute")
def checkout_stripe():
    if not load_site_settings().get("store_enabled", True):
        return jsonify({"ok": False, "error": "Checkout is temporarily closed."}), 400
    if not STRIPE_SECRET_KEY:
        return jsonify({"ok": False, "error": "Stripe is not configured."}), 400
    try:
        import stripe
        stripe.api_key = STRIPE_SECRET_KEY
        payload = request.get_json(silent=True) or {}
        cart = build_checkout_cart(payload.get("items") or [], payload.get("discount_code") or payload.get("code") or "", current_user())
        user = current_user() or {}
        order_id = f"ord_{secrets.token_hex(10)}"
        stripe_lines = []
        if cart.get("discount_cents", 0) > 0:
            stripe_lines.append({
                "quantity": 1,
                "price_data": {
                    "currency": STRIPE_CURRENCY,
                    "unit_amount": max(0, cart["subtotal_cents"] - cart.get("discount_cents", 0)),
                    "product_data": {"name": f"{APP_NAME} order after discounts"},
                },
            })
        else:
            for item in cart["items"]:
                stripe_lines.append({
                    "quantity": item["quantity"],
                    "price_data": {
                        "currency": STRIPE_CURRENCY,
                        "unit_amount": item["unit_cents"],
                        "product_data": {
                            "name": f"{item['product_name']} — {item['option_name']}",
                            "images": [request.url_root.rstrip("/") + item["image"]] if str(item.get("image", "")).startswith("/") else [],
                        },
                    },
                })
        if cart["fee_cents"] > 0:
            stripe_lines.append({
                "quantity": 1,
                "price_data": {
                    "currency": STRIPE_CURRENCY,
                    "unit_amount": cart["fee_cents"],
                    "product_data": {"name": f"Processing fee ({PROCESSING_FEE_PERCENT}%)"},
                },
            })
        save_order({
            "order_id": order_id,
            "user_email": user.get("email"),
            "username": user.get("username"),
            "provider": "stripe",
            "status": "pending",
            "expires_at": (utc_now() + timedelta(minutes=PENDING_ORDER_MAX_AGE_MINUTES)).isoformat(),
            "cart": cart,
            "amount_cents": cart["total_cents"],
            "currency": cart["currency"],
        })
        session_obj = stripe.checkout.Session.create(
            mode="payment",
            line_items=stripe_lines,
            customer_email=user.get("email"),
            metadata={"order_id": order_id, "user_email": user.get("email", "")},
            success_url=url_for("checkout_success", order_id=order_id, _external=True) + "&session_id={CHECKOUT_SESSION_ID}",
            cancel_url=url_for("checkout", _external=True),
        )
        save_order({
            "order_id": order_id,
            "user_email": user.get("email"),
            "username": user.get("username"),
            "provider": "stripe",
            "provider_session_id": object_get(session_obj, "id"),
            "status": "pending",
            "expires_at": (utc_now() + timedelta(minutes=PENDING_ORDER_MAX_AGE_MINUTES)).isoformat(),
            "cart": cart,
            "amount_cents": cart["total_cents"],
            "currency": cart["currency"],
        })
        checkout_url = object_get(session_obj, "url")
        if not checkout_url:
            raise RuntimeError("Stripe did not return a checkout URL.")
        return jsonify({"ok": True, "url": checkout_url, "order_id": order_id})
    except Exception as exc:
        logger.exception("Stripe checkout failed")
        message = str(exc) or exc.__class__.__name__
        if message == "get":
            message = "Stripe checkout session was created, but the app could not read the Stripe response. This has been patched; redeploy the updated code."
        return jsonify({"ok": False, "error": message}), 400


@app.route("/checkout/paypal/create", methods=["POST"])
@login_required
@limiter.limit("20 per minute")
def checkout_paypal_create():
    if not load_site_settings().get("store_enabled", True):
        return jsonify({"ok": False, "error": "Checkout is temporarily closed."}), 400
    if not (PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET):
        return jsonify({"ok": False, "error": "PayPal is not configured."}), 400
    try:
        payload = request.get_json(silent=True) or {}
        cart = build_checkout_cart(payload.get("items") or [], payload.get("discount_code") or payload.get("code") or "", current_user())
        user = current_user() or {}
        order_id = f"ord_{secrets.token_hex(10)}"
        token = paypal_access_token()
        resp = requests.post(
            f"{PAYPAL_API_BASE}/v2/checkout/orders",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={
                "intent": "CAPTURE",
                "application_context": {
                    "brand_name": APP_NAME[:127],
                    "landing_page": "LOGIN",
                    "shipping_preference": "NO_SHIPPING",
                    "user_action": "PAY_NOW",
                    "return_url": url_for("checkout_paypal_return", _external=True),
                    "cancel_url": url_for("checkout", _external=True),
                },
                "purchase_units": [{
                    "reference_id": order_id,
                    "custom_id": order_id,
                    "description": f"{APP_NAME} order",
                    "amount": {"currency_code": cart["currency"], "value": cart["total"]},
                }],
            },
            timeout=20,
        )
        resp.raise_for_status()
        pp = resp.json()
        approve_url = next((link.get("href") for link in pp.get("links", []) if link.get("rel") == "approve"), "")
        save_order({
            "order_id": order_id,
            "user_email": user.get("email"),
            "username": user.get("username"),
            "provider": "paypal",
            "provider_order_id": pp.get("id"),
            "status": "pending",
            "expires_at": (utc_now() + timedelta(minutes=PENDING_ORDER_MAX_AGE_MINUTES)).isoformat(),
            "cart": cart,
            "amount_cents": cart["total_cents"],
            "currency": cart["currency"],
        })
        if not approve_url:
            return jsonify({"ok": False, "error": "PayPal created the order but did not return an approval link."}), 400
        return jsonify({"ok": True, "id": pp.get("id"), "order_id": order_id, "approve_url": approve_url})
    except RuntimeError as exc:
        logger.warning("PayPal create order blocked: %s", exc)
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:
        logger.exception("PayPal create order failed")
        return jsonify({"ok": False, "error": "PayPal order creation failed. Check server logs."}), 400



@app.route("/checkout/paypal/return")
@login_required
@limiter.limit("20 per minute")
def checkout_paypal_return():
    paypal_order_id = (request.args.get("token") or "").strip()
    if not paypal_order_id:
        flash("PayPal did not return an order token. Please try again.", "error")
        return redirect(url_for("checkout"))
    order = find_order_by_provider_order(paypal_order_id)
    user = current_user() or {}
    if not order:
        flash("PayPal approved, but the local order was not found. Contact support with your PayPal order ID.", "error")
        return redirect(url_for("checkout"))
    if str(order.get("user_email", "")).lower() != str(user.get("email", "")).lower():
        flash("This PayPal order belongs to a different account.", "error")
        return redirect(url_for("checkout"))
    try:
        token = paypal_access_token()
        resp = requests.post(
            f"{PAYPAL_API_BASE}/v2/checkout/orders/{paypal_order_id}/capture",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            timeout=20,
        )
        if resp.status_code >= 400:
            body = resp.text[:240].replace("\n", " ")
            logger.warning("PayPal capture failed after return (%s): %s", resp.status_code, body)
            flash("PayPal approved but capture failed. Please contact support before retrying.", "error")
            return redirect(url_for("checkout"))
        data = resp.json()
        status = "paid" if data.get("status") == "COMPLETED" else "pending"
        paid_cents, paid_currency = extract_paypal_paid_amount_cents(data)
        if status == "paid" and not order_amount_matches(order, paid_cents, paid_currency):
            logger.warning("Rejected PayPal return amount mismatch for order=%s", order.get("order_id"))
            flash("PayPal amount verification failed. Contact support before retrying.", "error")
            return redirect(url_for("checkout"))
        update_order_status(order.get("order_id"), status, {"provider_payment_id": paypal_order_id, "paypal_capture": data})
        if status == "paid":
            flash("PayPal payment confirmed. Your order is ready in your account.", "success")
            return redirect(url_for("checkout_success", order_id=order.get("order_id")))
        flash("PayPal payment is pending. Check your account shortly.", "success")
        return redirect(url_for("checkout_success", order_id=order.get("order_id")))
    except RuntimeError as exc:
        flash(str(exc), "error")
        return redirect(url_for("checkout"))
    except Exception as exc:
        logger.exception("PayPal return capture failed")
        flash("PayPal return failed. Please contact support if the payment was taken.", "error")
        return redirect(url_for("checkout"))

@app.route("/checkout/paypal/capture", methods=["POST"])
@login_required
@limiter.limit("20 per minute")
def checkout_paypal_capture():
    try:
        payload = request.get_json(silent=True) or {}
        paypal_order_id = str(payload.get("paypal_order_id") or payload.get("orderID") or "").strip()
        internal_order_id = str(payload.get("order_id") or "").strip()
        if not paypal_order_id:
            return jsonify({"ok": False, "error": "Missing PayPal order ID."}), 400
        token = paypal_access_token()
        resp = requests.post(
            f"{PAYPAL_API_BASE}/v2/checkout/orders/{paypal_order_id}/capture",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        status = "paid" if data.get("status") == "COMPLETED" else "pending"
        if internal_order_id:
            order = find_order_by_id(internal_order_id)
            if not order or str(order.get("provider_order_id", "")) != paypal_order_id or str(order.get("user_email", "")).lower() != str((current_user() or {}).get("email", "")).lower():
                return jsonify({"ok": False, "error": "PayPal order verification failed."}), 403
            paid_cents, paid_currency = extract_paypal_paid_amount_cents(data)
            if status == "paid" and not order_amount_matches(order, paid_cents, paid_currency):
                return jsonify({"ok": False, "error": "PayPal amount verification failed."}), 403
            update_order_status(internal_order_id, status, {"provider_payment_id": paypal_order_id, "paypal_capture": data})
        return jsonify({"ok": True, "status": status, "order_id": internal_order_id})
    except RuntimeError as exc:
        logger.warning("PayPal capture blocked: %s", exc)
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:
        logger.exception("PayPal capture failed")
        return jsonify({"ok": False, "error": "PayPal capture failed. Check server logs."}), 400


@app.route("/webhooks/stripe", methods=["POST"])
@limiter.limit("120 per minute")
def stripe_webhook():
    if not STRIPE_SECRET_KEY:
        return jsonify({"ok": False}), 400
    try:
        import stripe
        payload = request.data
        sig_header = request.headers.get("Stripe-Signature")
        if STRIPE_WEBHOOK_SECRET:
            event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
        elif IS_PRODUCTION:
            logger.warning("Stripe webhook rejected because STRIPE_WEBHOOK_SECRET is missing in production.")
            return jsonify({"ok": False}), 400
        else:
            event = request.get_json(force=True)
        if object_get(event, "type") == "checkout.session.completed":
            event_data = object_get(event, "data", {}) or {}
            session_obj = object_get(event_data, "object", {}) or {}
            metadata = object_get(session_obj, "metadata", {}) or {}
            order_id = object_get(metadata, "order_id", "")
            if order_id:
                order = find_order_by_id(order_id)
                paid_cents = object_get(session_obj, "amount_total")
                paid_currency = str(object_get(session_obj, "currency", "")).upper()
                if order and str(order.get("provider_session_id", "")) == str(object_get(session_obj, "id")) and order_amount_matches(order, paid_cents, paid_currency):
                    update_order_status(order_id, "paid", {"provider_payment_id": object_get(session_obj, "id")})
                else:
                    logger.warning("Rejected Stripe webhook amount/session mismatch for order=%s", order_id)
        return jsonify({"received": True})
    except Exception as exc:
        logger.warning("Stripe webhook rejected: %s", exc)
        return jsonify({"ok": False}), 400



@app.route("/admin/order/<order_id>/status", methods=["POST"])
@owner_required
@limiter.limit("30 per minute")
def admin_order_change_status(order_id):
    order = find_order_by_id(order_id)
    if not order:
        flash("Order not found.", "danger")
        return redirect(url_for("admin_dashboard") + "#orders")

    new_status = normalize_order_status(request.form.get("status"))
    if not new_status:
        flash("That order status is not allowed.", "danger")
        return redirect(url_for("admin_dashboard") + "#orders")

    previous_status = normalize_order_status(order.get("status")) or "pending"
    reason = (request.form.get("status_note") or "").strip()[:500]
    changed_at = utc_now().isoformat()
    history = list(order.get("status_history") or [])[-24:]
    history.append({
        "from": previous_status,
        "to": new_status,
        "at": changed_at,
        "by": (current_user() or {}).get("email") or "owner",
        "note": reason,
    })
    updated = update_order_status(order_id, new_status, {
        "status_source": "admin",
        "status_note": reason,
        "status_changed_at": changed_at,
        "status_history": history,
    })
    if not updated:
        flash("The order status could not be updated.", "danger")
        return redirect(url_for("admin_dashboard") + "#orders")

    record_audit("order_status_changed", order_id, {
        "from": previous_status,
        "to": new_status,
        "note": reason,
    })
    flash(f"Order {order_id} changed from {previous_status} to {new_status}.", "success")
    return redirect(url_for("admin_dashboard") + "#orders")


@app.route("/admin/order/<order_id>/deliver-key", methods=["POST"])
@owner_required
@limiter.limit("30 per minute")
def admin_order_deliver_key(order_id):
    order = find_order_by_id(order_id)
    if not order:
        flash("Order not found.", "danger")
        return redirect(url_for("admin_dashboard") + "#orders")
    if str(order.get("status", "")).lower() != "paid":
        flash("Only paid orders can receive keys.", "danger")
        return redirect(url_for("admin_dashboard") + "#orders")
    try:
        item_index = int(request.form.get("item_index", "0"))
    except Exception:
        item_index = 0
    cart_items = (order.get("cart") or {}).get("items") or []
    if item_index < 0 or item_index >= len(cart_items):
        flash("Invalid order item.", "danger")
        return redirect(url_for("admin_dashboard") + "#orders")
    product_key = (request.form.get("product_key") or "").strip()
    note = (request.form.get("delivery_note") or "").strip()[:1000]
    if len(product_key) < 3:
        flash("Enter a valid key before sending.", "danger")
        return redirect(url_for("admin_dashboard") + "#orders")
    item = cart_items[item_index]
    delivery = save_delivery_to_order(
        order,
        item,
        item_index,
        product_key,
        note,
        (current_user() or {}).get("email") or "manual",
    )
    notify_buyer_delivery(order, item, delivery)
    order["delivery_status"] = "delivered" if len(order.get("deliveries") or {}) >= len(cart_items) else "partial"
    order["updated_at"] = utc_now().isoformat()
    save_order(order)
    record_audit("order_key_delivered", order_id, {"item_index": item_index, "email_sent": delivery.get("email_sent"), "discord_dm_sent": delivery.get("discord_dm_sent")})
    msg = "Key saved to the customer's account"
    msg += ", email sent" if delivery.get("email_sent") else ", email not sent"
    if (get_user_by_email(order.get("user_email")) or {}).get("discord_id"):
        msg += ", Discord DM sent" if delivery.get("discord_dm_sent") else ", Discord DM failed"
    flash(msg + ".", "success" if delivery.get("email_sent") or delivery.get("discord_dm_sent") else "warning")
    return redirect(url_for("admin_dashboard") + "#orders")


@app.route("/admin/api/diagnostics")
@owner_required
def admin_api_diagnostics():
    return jsonify({
        "ok": True,
        "storage": "mongodb" if using_mongo() else "json_fallback",
        "mongo_status": mongo_status_reason,
        "stripe": bool(STRIPE_SECRET_KEY),
        "paypal": bool(PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET),
        "discord_oauth": discord_oauth_ready(),
        "discord_bot": bool(DISCORD_BOT_TOKEN),
        "owner_order_webhook": bool(OWNER_ORDER_WEBHOOK_URL),
        "delivery_dm_enabled": bool(DELIVERY_DM_ENABLED and DISCORD_BOT_TOKEN),
        "email_codes": bool(email_delivery_ready() and REQUIRE_EMAIL_CODES),
        "email_provider": EMAIL_PROVIDER,
        "resend": bool(RESEND_API_KEY),
        "reselling_pro": bool(RESELLING_PRO_ENABLED and RESELLING_PRO_API_KEY),
        "maintenance": is_maintenance_mode(),
        "store_enabled": bool(load_site_settings().get("store_enabled", True)),
        "discounts_enabled": bool(get_discount_settings().get("enabled", True)),
    })

# -----------------------------------------------------------------------------
# Legal pages
# -----------------------------------------------------------------------------
@app.route("/legal")
def legal_center():
    return render_template("legal_center.html", active_page="legal")


@app.route("/terms")
def terms():
    return render_template("terms.html", active_page="legal")


@app.route("/privacy")
def privacy():
    return render_template("privacy.html", active_page="legal")


@app.route("/refund-policy")
def refund_policy():
    return render_template("refund_policy.html", active_page="legal")


@app.route("/cookies")
@app.route("/cookie-policy")
def cookie_policy():
    return render_template("cookie_policy.html", active_page="legal")


@app.route("/license")
def license_page():
    return render_template("license.html", active_page="legal")


@app.route("/acceptable-use")
def acceptable_use():
    return render_template("acceptable_use.html", active_page="legal")


@app.route("/dmca")
def dmca_policy():
    return render_template("dmca.html", active_page="legal")


# -----------------------------------------------------------------------------
# API routes
# -----------------------------------------------------------------------------
@app.route("/api/products")
@limiter.limit("120 per minute")
def api_products():
    return jsonify(load_products())


@app.route("/api/store-products")
@limiter.limit("120 per minute")
def api_store_products():
    if not load_site_settings().get("store_enabled", True):
        return jsonify([])
    return jsonify(filter_products("store"))


@app.route("/api/store-products/<slug>")
@limiter.limit("120 per minute")
def api_store_product(slug):
    product = find_store_product(slug)

    if not product:
        abort(404)

    return jsonify(product)

@app.route("/api/downloads")
@limiter.limit("120 per minute")
def api_downloads():
    return jsonify(filter_products("downloads"))


@app.route("/api/status")
@limiter.limit("120 per minute")
def api_status():
    return jsonify(filter_products("status"))


# -----------------------------------------------------------------------------
# Downloads
# -----------------------------------------------------------------------------
@app.route("/download/<path:filename>")
@limiter.limit("30 per minute")
def download_file(filename):
    safe_path = safe_join(str(FILES_DIR), filename)

    if not safe_path:
        abort(404)

    allowed_files = get_allowed_download_files()
    normalized_filename = filename.strip("/")

    if normalized_filename not in allowed_files:
        abort(404)

    file_path = Path(safe_path)

    if file_path.exists() and file_path.is_file():
        return send_from_directory(
            directory=str(FILES_DIR),
            path=normalized_filename,
            as_attachment=True,
            conditional=True,
        )

    return send_mongo_stored_file(normalized_filename, as_attachment=True)


# -----------------------------------------------------------------------------
# Health
# -----------------------------------------------------------------------------

@app.route("/service-worker.js")
def service_worker():
    response = send_from_directory(app.static_folder, "service-worker.js")
    response.headers["Content-Type"] = "application/javascript; charset=utf-8"
    response.headers["Cache-Control"] = "no-cache"
    response.headers["Service-Worker-Allowed"] = "/"
    return response

@app.route("/health")
@app.route("/healthz")
@limiter.exempt
def health_check():
    return jsonify({
        "status": "healthy",
        "message": "Service is running",
        "environment": FLASK_ENV,
        "payments": {"stripe": bool(STRIPE_SECRET_KEY), "paypal": bool(PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET)},
    })


# -----------------------------------------------------------------------------
# Optional dynamic routes
# -----------------------------------------------------------------------------
def register_dynamic_routes():
    """
    Placeholder for future dynamic route loading.
    Safe no-op so startup does not crash if routes.json logic is not ready yet.
    """
    return None


register_dynamic_routes()

# Secure desktop loader API
def authenticate_loader_password(email: str, password: str):
    email = (email or "").strip().lower()
    if using_mongo():
        user = users_col.find_one({"email": email})
        if not user or not check_password_hash(user.get("password_hash", ""), password):
            return None
        if str(user.get("status") or "active").lower() == "suspended":
            return None
        return user
    if email == OWNER_EMAIL and password == OWNER_PASSWORD:
        return {"email": OWNER_EMAIL, "username": OWNER_USERNAME, "is_owner": True, "role": "owner", "status": "active"}
    return None

from loader_api import register_loader_api
register_loader_api(
    app,
    current_user=current_user,
    load_products=load_products,
    load_orders_for_user=load_orders_for_user,
    get_user_by_email=get_user_by_email,
    authenticate_password=authenticate_loader_password,
    files_dir=FILES_DIR,
    using_mongo=using_mongo,
    db=db,
    load_support_tickets_for_user=load_support_tickets_for_user,
    find_support_ticket=find_support_ticket,
    save_support_ticket=save_support_ticket,
    append_ticket_message=append_ticket_message,
    ticket_public_id=ticket_public_id,
    send_security_email=send_security_email,
    require_email_codes=REQUIRE_EMAIL_CODES,
)


# -----------------------------------------------------------------------------
# App start
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))
    debug = not IS_PRODUCTION

    logger.info("Starting application on port %d (debug=%s)", port, debug)

    if IS_PRODUCTION:
        from waitress import serve
        serve(app, host="0.0.0.0", port=port, threads=8)
    else:
        app.run(
            host="0.0.0.0",
            port=port,
            debug=debug,
            use_reloader=debug,
        )
