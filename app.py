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
from urllib.parse import urlencode
from functools import wraps
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, abort, flash, jsonify, redirect, render_template, request, send_from_directory, session, url_for
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
SETTINGS_FILE = DATA_DIR / "site_settings.json"
ALLOWED_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}
ALLOWED_FILE_EXTENSIONS = {"zip", "rar", "7z", "pdf", "txt", "json", "png", "jpg", "jpeg", "gif", "webp"}
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "30"))

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
SMTP_EMAIL = os.getenv("SMTP_EMAIL", "moealturej@gmail.com").strip()
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
APP_NAME = os.getenv("APP_NAME", "moealturej").strip() or "moealturej"
APP_URL = os.getenv("APP_URL", "https://moealturej.com").strip().rstrip("/")
SUPPORT_EMAIL = os.getenv("SUPPORT_EMAIL", SMTP_EMAIL or OWNER_EMAIL).strip()
BRAND_LOGO_URL = os.getenv("BRAND_LOGO_URL", "").strip()

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

def init_mongo():
    global mongo_client, db, mongo_status_reason, products_col, users_col, settings_col, media_col, audit_col, orders_col
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
        mongo_client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
        mongo_client.admin.command("ping")
        db = mongo_client[MONGO_DB_NAME]
        products_col = db["products"]
        users_col = db["users"]
        settings_col = db["settings"]
        media_col = db["media"]
        audit_col = db["audit_logs"]
        orders_col = db["orders"]
        users_col.create_index([("email", ASCENDING)], unique=True)
        products_col.create_index([("slug", ASCENDING)], unique=True)
        media_col.create_index([("filename", ASCENDING)], unique=True)
        audit_col.create_index([("created_at", ASCENDING)])
        orders_col.create_index([("order_id", ASCENDING)], unique=True)
        orders_col.create_index([("user_email", ASCENDING)])
        mongo_status_reason = f"Connected to MongoDB database {MONGO_DB_NAME}."
        logger.info("Connected to MongoDB database %s", MONGO_DB_NAME)
    except Exception as exc:
        mongo_status_reason = f"MongoDB connection failed: {exc}. Check MONGO_URI, username/password, IP whitelist, and your Atlas cluster hostname."
        logger.error("MongoDB connection failed (%s). Falling back to local JSON mode. Check MONGO_URI in .env.", exc)
        mongo_client = db = products_col = users_col = settings_col = media_col = audit_col = orders_col = None
media_col = None
audit_col = None
orders_col = None

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

def current_user():
    return session.get("user")

def has_admin_access(user: dict | None) -> bool:
    return bool(user and (user.get("is_owner") or user.get("role") in {"owner", "admin"}))

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
    SEND_FILE_MAX_AGE_DEFAULT=31536000 if IS_PRODUCTION else 300,
    SMTP_EMAIL=SMTP_EMAIL,
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


def send_security_email(to_email: str, code: str, purpose: str) -> bool:
    safe_purpose = html_escape.escape(str(purpose or "security").strip().title())
    safe_code = html_escape.escape(str(code))
    safe_app = html_escape.escape(APP_NAME)
    safe_app_url = html_escape.escape(APP_URL)
    safe_support = html_escape.escape(SUPPORT_EMAIL)
    safe_logo = html_escape.escape(BRAND_LOGO_URL)
    subject = f"{APP_NAME} security code: {code}"
    text = (
        f"Your {APP_NAME} {purpose} code is: {code}\n\n"
        "This code expires in 10 minutes.\n"
        f"Open {APP_URL} if you requested this.\n\n"
        f"If this was not you, ignore this email and contact {SUPPORT_EMAIL}."
    )
    logo_html = (
        f'<img src="{safe_logo}" alt="{safe_app}" width="46" height="46" '
        'style="display:block;border-radius:14px;object-fit:cover;box-shadow:0 12px 30px rgba(172,89,255,.35);">'
        if safe_logo else
        f'<div style="width:46px;height:46px;border-radius:14px;background:linear-gradient(135deg,#8b5cf6,#ec4899);display:grid;place-items:center;color:#fff;font-weight:900;font-size:20px;box-shadow:0 12px 30px rgba(172,89,255,.35);">m</div>'
    )
    html = f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <title>{safe_app} security code</title>
  </head>
  <body style="margin:0;background:#05020a;color:#ffffff;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif;">
    <div style="display:none;max-height:0;overflow:hidden;opacity:0;color:transparent;">Your {safe_app} {safe_purpose.lower()} code is {safe_code}. It expires in 10 minutes.</div>
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:radial-gradient(circle at top left,rgba(139,92,246,.25),transparent 34%),radial-gradient(circle at top right,rgba(236,72,153,.18),transparent 32%),#05020a;padding:34px 14px;">
      <tr>
        <td align="center">
          <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:620px;border-collapse:separate;border-spacing:0;background:linear-gradient(180deg,rgba(24,12,39,.98),rgba(8,5,15,.98));border:1px solid rgba(255,255,255,.12);border-radius:28px;overflow:hidden;box-shadow:0 28px 90px rgba(0,0,0,.55);">
            <tr>
              <td style="padding:26px 28px;border-bottom:1px solid rgba(255,255,255,.08);">
                <table role="presentation" width="100%" cellspacing="0" cellpadding="0">
                  <tr>
                    <td style="width:58px;vertical-align:middle;">{logo_html}</td>
                    <td style="vertical-align:middle;">
                      <div style="font-size:20px;line-height:1.2;font-weight:900;letter-spacing:-.03em;color:#ffffff;">{safe_app}</div>
                      <div style="font-size:13px;line-height:1.5;color:#b9a8d8;">Secure account verification</div>
                    </td>
                  </tr>
                </table>
              </td>
            </tr>
            <tr>
              <td style="padding:34px 28px 16px;text-align:center;">
                <div style="display:inline-block;padding:8px 13px;border-radius:999px;background:rgba(139,92,246,.16);border:1px solid rgba(216,180,254,.22);color:#e9d5ff;font-size:12px;font-weight:800;letter-spacing:.08em;text-transform:uppercase;">{safe_purpose} code</div>
                <h1 style="margin:18px 0 10px;font-size:32px;line-height:1.1;letter-spacing:-.05em;color:#ffffff;">Finish signing in securely</h1>
                <p style="margin:0 auto;max-width:420px;color:#b9a8d8;font-size:15px;line-height:1.65;">Use the code below to continue. This helps keep your {safe_app} account protected.</p>
              </td>
            </tr>
            <tr>
              <td style="padding:18px 28px 12px;text-align:center;">
                <div style="display:inline-block;min-width:265px;padding:20px 18px;border-radius:22px;background:linear-gradient(135deg,rgba(139,92,246,.22),rgba(236,72,153,.14));border:1px solid rgba(255,255,255,.14);box-shadow:inset 0 1px 0 rgba(255,255,255,.12),0 18px 55px rgba(139,92,246,.18);">
                  <div style="font-size:40px;line-height:1;font-weight:950;letter-spacing:12px;color:#ffffff;text-shadow:0 0 28px rgba(216,180,254,.35);">{safe_code}</div>
                </div>
                <p style="margin:18px 0 0;color:#f0abfc;font-size:14px;font-weight:700;">Expires in 10 minutes</p>
              </td>
            </tr>
            <tr>
              <td style="padding:12px 28px 30px;text-align:center;">
                <a href="{safe_app_url}" style="display:inline-block;text-decoration:none;color:#ffffff;background:linear-gradient(135deg,#7c3aed,#db2777);padding:13px 20px;border-radius:14px;font-size:14px;font-weight:900;box-shadow:0 16px 35px rgba(124,58,237,.28);">Open {safe_app}</a>
              </td>
            </tr>
            <tr>
              <td style="padding:20px 28px 26px;background:rgba(255,255,255,.035);border-top:1px solid rgba(255,255,255,.08);">
                <p style="margin:0 0 8px;color:#c4b5fd;font-size:13px;line-height:1.6;"><strong style="color:#fff;">Didn’t request this?</strong> You can ignore this email. Nobody can access your account without this code.</p>
                <p style="margin:0;color:#8f80ad;font-size:12px;line-height:1.6;">Need help? Contact <a href="mailto:{safe_support}" style="color:#e9d5ff;text-decoration:none;">{safe_support}</a>.</p>
              </td>
            </tr>
          </table>
          <div style="max-width:620px;margin:16px auto 0;color:#6f6288;font-size:11px;line-height:1.6;text-align:center;">This automated security email was sent by {safe_app}. Never share this code with anyone.</div>
        </td>
      </tr>
    </table>
  </body>
</html>
"""
    if not SMTP_EMAIL or not SMTP_PASSWORD:
        if IS_PRODUCTION:
            logger.error("SMTP_PASSWORD is missing, cannot send security code in production.")
            return False
        flash(f"Dev security code: {code}", "warning")
        return True
    try:
        msg = EmailMessage()
        msg["From"] = f"{APP_NAME} Security <{SMTP_EMAIL}>"
        msg["To"] = to_email
        msg["Subject"] = subject
        msg.set_content(text)
        msg.add_alternative(html, subtype="html")
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=context, timeout=15) as server:
            server.login(SMTP_EMAIL, SMTP_PASSWORD)
            server.send_message(msg)
        return True
    except Exception:
        logger.exception("Failed to send security email to %s", to_email)
        return False


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
    session["user"] = {
        "email": user.get("email"),
        "username": user.get("username") or user.get("email", "user").split("@")[0],
        "is_owner": bool(user.get("is_owner") or user.get("role") == "owner"),
        "role": user.get("role", "owner" if user.get("is_owner") else "user"),
        "auth_provider": user.get("auth_provider", "email"),
        "discord_id": user.get("discord_id"),
        "discord_avatar": user.get("discord_avatar"),
    }
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
    if not SMTP_PASSWORD:
        logger.warning("SMTP_PASSWORD is not configured; cannot send password reset email.")
        return False
    safe_app = html_escape.escape(APP_NAME)
    safe_url = html_escape.escape(reset_url)
    safe_support = html_escape.escape(SUPPORT_EMAIL)
    msg = EmailMessage()
    msg["Subject"] = f"Reset your {APP_NAME} password"
    msg["From"] = f"{APP_NAME} Security <{SMTP_EMAIL}>"
    msg["To"] = to_email
    text = (
        f"Reset your {APP_NAME} password using this secure link: {reset_url}\n\n"
        "This link expires in 20 minutes. If you did not request it, ignore this email."
    )
    html = f"""<!doctype html><html><body style='margin:0;background:#05020a;color:#fff;font-family:Arial,sans-serif;'><table width='100%' cellpadding='0' cellspacing='0' style='padding:34px 14px;background:#05020a;'><tr><td align='center'><table width='100%' cellpadding='0' cellspacing='0' style='max-width:620px;background:linear-gradient(180deg,#160b28,#07030d);border:1px solid rgba(255,255,255,.12);border-radius:26px;overflow:hidden;'><tr><td style='padding:28px;border-bottom:1px solid rgba(255,255,255,.08);'><div style='font-size:22px;font-weight:900;letter-spacing:-.03em;'>{safe_app}</div><div style='color:#b9a8d8;font-size:13px;margin-top:5px;'>Password recovery</div></td></tr><tr><td style='padding:34px 28px;text-align:center;'><div style='display:inline-block;padding:8px 13px;border:1px solid rgba(216,180,254,.28);border-radius:999px;color:#e9d5ff;font-size:12px;font-weight:800;letter-spacing:.08em;text-transform:uppercase;'>Secure reset</div><h1 style='font-size:32px;line-height:1.1;margin:18px 0 10px;color:#fff;'>Reset your password</h1><p style='color:#b9a8d8;line-height:1.65;margin:0 auto 24px;max-width:420px;'>Use the button below to create a new password. This link expires in 20 minutes.</p><a href='{safe_url}' style='display:inline-block;text-decoration:none;color:#fff;background:linear-gradient(135deg,#7c3aed,#db2777);padding:14px 22px;border-radius:14px;font-weight:900;'>Reset password</a><p style='margin:24px 0 0;color:#7e7191;font-size:12px;line-height:1.6;'>Did not request this? Ignore this email or contact {safe_support}.</p></td></tr></table></td></tr></table></body></html>"""
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    try:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=context) as server:
            server.login(SMTP_EMAIL, SMTP_PASSWORD)
            server.send_message(msg)
        return True
    except Exception:
        logger.exception("Failed to send password reset email to %s", to_email)
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


def allowed_upload(filename: str, upload_kind: str) -> bool:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if upload_kind == "image":
        return ext in ALLOWED_IMAGE_EXTENSIONS
    return ext in ALLOWED_FILE_EXTENSIONS


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
            return [normalize_product(p) for p in products_col.find({}, {"_id": 0}).sort("name", 1)]
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
        return [normalize_product(p) for p in data if isinstance(p, dict)]
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


def build_checkout_cart(raw_items: list) -> dict:
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
        })
    if not line_items:
        raise ValueError("No valid products are in the cart.")
    fee_cents = int(round(subtotal_cents * (PROCESSING_FEE_PERCENT / 100)))
    total_cents = subtotal_cents + fee_cents
    return {
        "items": line_items,
        "subtotal_cents": subtotal_cents,
        "fee_cents": fee_cents,
        "total_cents": total_cents,
        "subtotal": cents_to_money(subtotal_cents),
        "fee": cents_to_money(fee_cents),
        "total": cents_to_money(total_cents),
        "currency": STRIPE_CURRENCY.upper(),
        "quantity": total_quantity,
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

def update_order_status(order_id: str, status: str, details: dict | None = None):
    orders = load_orders_for_user(None)
    existing = next((o for o in orders if o.get("order_id") == order_id), None) or {"order_id": order_id}
    existing["status"] = status
    existing["updated_at"] = utc_now().isoformat()
    if details:
        existing.update(details)
    save_order(existing)


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



def default_site_settings() -> dict:
    return {
        "maintenance_enabled": False,
        "store_enabled": True,
        "registration_enabled": True,
        "announcement_enabled": False,
        "announcement_text": "",
        "announcement_type": "info",
        "support_url": "/support",
        "discord_url": "",
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
        "site_settings": load_site_settings(),
    }


# -----------------------------------------------------------------------------
# Request hooks
# -----------------------------------------------------------------------------
@app.before_request
def set_session_timeout():
    session.permanent = True
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
    if user.get("is_owner") or user.get("role") == "admin":
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
    response.headers["Cache-Control"] = response.headers.get("Cache-Control", "no-store" if request.path.startswith(("/admin", "/account", "/checkout")) else response.headers.get("Cache-Control", ""))

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
    order_id = request.args.get("order_id", "")
    stripe_session_id = request.args.get("session_id", "")
    if stripe_session_id and STRIPE_SECRET_KEY:
        try:
            import stripe
            stripe.api_key = STRIPE_SECRET_KEY
            checkout_session = stripe.checkout.Session.retrieve(stripe_session_id)
            if checkout_session.get("payment_status") == "paid":
                update_order_status(order_id or checkout_session.get("metadata", {}).get("order_id", ""), "paid", {"provider_payment_id": stripe_session_id})
                flash("Payment confirmed. Your order is ready in your account.", "success")
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
    return render_template("support.html", active_page="support")


@app.route("/maintenance")
def maintenance():
    return render_template("maintenance.html", active_page=None), 503

@app.route("/donate")
def donate():
    return render_template("donate.html", active_page="donate")


@app.route("/discord")
def discord():
    return render_template("discord.html", active_page="discord")


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
                flash("Email verification is not configured. Add SMTP_PASSWORD for moealturej@gmail.com.", "danger")
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
                flash("Email 2FA is not configured. Add SMTP_PASSWORD for moealturej@gmail.com.", "danger")
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
                flash("Password reset email is not configured. Add SMTP_PASSWORD for Gmail app password.", "danger")
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
    if not current_user():
        return redirect(url_for("login", next=request.path))
    orders = load_orders_for_user((current_user() or {}).get("email"))
    return render_template("account.html", active_page="account", orders=orders)

@app.route("/owner")
@app.route("/admin")
@owner_required
def admin_dashboard():
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
        "payments": ("Stripe " if STRIPE_SECRET_KEY else "") + ("PayPal" if PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET else "") or "not configured",
    }
    return render_template("admin.html", products=products, users=load_admin_users(), media_items=load_media(), orders=load_orders_for_user(None), stats=stats, site_settings=load_site_settings(), mongo_status_reason=mongo_status_reason, active_page="admin")


@app.route("/admin/settings", methods=["POST"])
@owner_required
def admin_site_settings():
    current = load_site_settings()
    current.update({
        "maintenance_enabled": bool(request.form.get("maintenance_enabled")),
        "store_enabled": bool(request.form.get("store_enabled")),
        "registration_enabled": bool(request.form.get("registration_enabled")),
        "announcement_enabled": bool(request.form.get("announcement_enabled")),
        "announcement_text": request.form.get("announcement_text", "").strip()[:300],
        "announcement_type": request.form.get("announcement_type", "info").strip()[:20],
        "support_url": request.form.get("support_url", "/support").strip()[:300] or "/support",
        "discord_url": request.form.get("discord_url", "").strip()[:300],
    })
    save_site_settings(current)
    record_audit("site_settings_update", "site", current)
    flash("Site settings updated.", "success")
    return redirect(url_for("admin_dashboard") + "#owner")

@app.route("/admin/product/new", methods=["POST"])
@owner_required
def admin_product_new():
    name = request.form.get("name", "New Product").strip() or "New Product"
    slug = clean_slug(request.form.get("slug") or name)
    product = normalize_product({
        "id": int(utc_now().timestamp() * 1000),
        "slug": slug,
        "name": name,
        "detailedDescription": request.form.get("description", ""),
        "image": request.form.get("image", "/static/logo.png"),
        "category": request.form.get("category", "general"),
        "type": "product",
        "featured": bool(request.form.get("featured")),
        "features": [],
        "store": {"enabled": bool(request.form.get("store_enabled")), "stockStatus": "In Stock", "options": []},
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
    role = request.form.get("role", "user")
    actor = current_user() or {}
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
    upload.save(target_dir / filename)
    url = url_for("media_file", filename=filename) if upload_kind == "image" else url_for("download_file", filename=filename)
    record = {
        "filename": filename,
        "original_name": original,
        "kind": upload_kind,
        "url": url,
        "mime_type": mimetypes.guess_type(original)[0] or "application/octet-stream",
        "size_bytes": (target_dir / filename).stat().st_size,
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
    if not file_path.exists() or not file_path.is_file():
        abort(404)
    return send_from_directory(str(UPLOADS_DIR), safe_name, conditional=True)

# -----------------------------------------------------------------------------
# Checkout / Payments
# -----------------------------------------------------------------------------
@app.route("/api/checkout/preview", methods=["POST"])
@limiter.limit("60 per minute")
def api_checkout_preview():
    try:
        payload = request.get_json(silent=True) or {}
        cart = build_checkout_cart(payload.get("items") or [])
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
        cart = build_checkout_cart(payload.get("items") or [])
        user = current_user() or {}
        order_id = f"ord_{secrets.token_hex(10)}"
        stripe_lines = []
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
            "provider_session_id": session_obj.get("id"),
            "status": "pending",
            "cart": cart,
            "amount_cents": cart["total_cents"],
            "currency": cart["currency"],
        })
        return jsonify({"ok": True, "url": session_obj.url, "order_id": order_id})
    except Exception as exc:
        logger.exception("Stripe checkout failed")
        return jsonify({"ok": False, "error": str(exc)}), 400


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
        cart = build_checkout_cart(payload.get("items") or [])
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
        else:
            event = request.get_json(force=True)
        if event.get("type") == "checkout.session.completed":
            session_obj = event.get("data", {}).get("object", {})
            order_id = (session_obj.get("metadata") or {}).get("order_id", "")
            if order_id:
                update_order_status(order_id, "paid", {"provider_payment_id": session_obj.get("id")})
        return jsonify({"received": True})
    except Exception as exc:
        logger.warning("Stripe webhook rejected: %s", exc)
        return jsonify({"ok": False}), 400


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
        "email_codes": bool(SMTP_EMAIL and SMTP_PASSWORD and REQUIRE_EMAIL_CODES),
        "maintenance": is_maintenance_mode(),
        "store_enabled": bool(load_site_settings().get("store_enabled", True)),
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

    if not file_path.exists() or not file_path.is_file():
        abort(404)

    return send_from_directory(
        directory=str(FILES_DIR),
        path=normalized_filename,
        as_attachment=True,
        conditional=True,
    )


# -----------------------------------------------------------------------------
# Health
# -----------------------------------------------------------------------------
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
