import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, abort, jsonify, render_template, request, send_from_directory, session
from flask_compress import Compress
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.utils import safe_join


# -----------------------------------------------------------------------------
# Environment / Setup
# -----------------------------------------------------------------------------
load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
STATIC_DIR = BASE_DIR / "static"
PRODUCTS_FILE = DATA_DIR / "products.json"
FILES_DIR = DATA_DIR / "files"

FLASK_ENV = os.getenv("FLASK_ENV", "development").lower()
IS_PRODUCTION = FLASK_ENV == "production"

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
    "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://cdn.sell.app https://sellauth.com; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdnjs.cloudflare.com; "
    "img-src 'self' data: https:; "
    "font-src 'self' data: https://fonts.gstatic.com https://cdnjs.cloudflare.com; "
    "connect-src 'self' https://cdn.jsdelivr.net https://api-internal-2.sellauth.com https://api-internal-3.sellauth.com https://sellauth.com https://formspree.io; "
    "frame-src https://*.mysellauth.com https://sellauth.com; "
    "worker-src 'self' blob:; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'none';"
)

# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
def load_products() -> list:
    """Safely load product data from JSON."""
    if not PRODUCTS_FILE.exists():
        logger.warning("Products file does not exist: %s", PRODUCTS_FILE)
        return []

    try:
        with PRODUCTS_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)

        if not isinstance(data, list):
            logger.warning("Products JSON is not a list.")
            return []

        return data

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


def is_maintenance_mode() -> bool:
    return os.getenv("MAINTENANCE_MODE", "false").lower() == "true"


# -----------------------------------------------------------------------------
# Template globals
# -----------------------------------------------------------------------------
@app.context_processor
def inject_global_template_vars():
    return {
        "current_year": datetime.now().year,
    }


# -----------------------------------------------------------------------------
# Request hooks
# -----------------------------------------------------------------------------
@app.before_request
def set_session_timeout():
    session.permanent = True


@app.before_request
def check_maintenance():
    if not is_maintenance_mode():
        return None

    allowed_paths = {
        "/maintenance",
        "/health",
    }

    if request.path.startswith("/static/"):
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
    return render_template("store.html", active_page="products")


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
    return jsonify(filter_products("store"))


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
@limiter.exempt
def health_check():
    return jsonify({
        "status": "healthy",
        "message": "Service is running",
        "environment": FLASK_ENV,
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