from __future__ import annotations
import os
import json
import logging
import secrets
from datetime import timedelta
from pathlib import Path
from typing import Any, Dict, List

from flask import (
    Flask, session, render_template, abort, g, request, make_response
)
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from werkzeug.middleware.proxy_fix import ProxyFix

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"
ROUTES_PATH = STATIC_DIR / "data" / "routes.json"

app = Flask(__name__, template_folder=str(TEMPLATES_DIR), static_folder=str(STATIC_DIR))

secret_key = os.getenv("SECRET_KEY")
if not secret_key:
    raise RuntimeError("SECRET_KEY environment variable is not set")
app.secret_key = secret_key

trust_proxy = int(os.getenv("TRUST_PROXY", "0"))
if trust_proxy > 0:
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=trust_proxy, x_proto=trust_proxy, x_host=trust_proxy, x_port=trust_proxy, x_prefix=trust_proxy)

DEBUG_MODE = os.getenv("DEBUG_MODE", "false").lower() == "true"
FORCE_HTTPS = os.getenv("FORCE_HTTPS", "true").lower() == "true"

SESSION_SAMESITE = os.getenv("SESSION_SAMESITE", "None")
SESSION_SECURE = os.getenv("SESSION_SECURE", "true").lower() == "true"

app.config.update(
    PERMANENT_SESSION_LIFETIME=timedelta(minutes=30),
    SESSION_COOKIE_SECURE=SESSION_SECURE and FORCE_HTTPS and not DEBUG_MODE,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE=SESSION_SAMESITE,
)

logger = logging.getLogger("app")
logger.setLevel(logging.INFO)
handler = logging.StreamHandler()
formatter = logging.Formatter('%(asctime)s %(levelname)s %(name)s %(message)s')
handler.setFormatter(formatter)
if not logger.handlers:
    logger.addHandler(handler)

redis_url = os.getenv("REDIS_URL")
if redis_url:
    storage_uri = redis_url
else:
    storage_uri = "memory://"

def client_key() -> str:
    ip = get_remote_address()
    ua = request.headers.get("User-Agent", "?")
    return f"{ip}:{hash(ua)}"

limiter = Limiter(
    key_func=client_key,
    app=app,
    default_limits=["8000 per day", "2000 per hour", "120 per minute"],
    storage_uri=storage_uri,
    strategy="moving-window",
)

ALLOWED_SCRIPT_SRCS = ["'self'", "https://cdn.jsdelivr.net", "https://cdn.sell.app", "https://sellauth.com"]
ALLOWED_STYLE_SRCS = ["'self'", "'unsafe-inline'", "https://fonts.googleapis.com", "https://cdnjs.cloudflare.com"]
ALLOWED_IMG_SRCS = ["'self'", "data:", "https://i.postimg.cc", "https://sellauth.com"]
ALLOWED_FONT_SRCS = ["'self'", "https://fonts.gstatic.com", "https://cdnjs.cloudflare.com"]
ALLOWED_CONNECT_SRCS = ["'self'", "https://api-internal-2.sellauth.com", "https://sellauth.com", "https://formspree.io"]
ALLOWED_FRAME_SRCS = ["https://*.mysellauth.com", "https://sellauth.com"]

def build_csp(nonce: str) -> str:
    script_src = " ".join(ALLOWED_SCRIPT_SRCS + [f"'nonce-{nonce}'"])
    style_src = " ".join(ALLOWED_STYLE_SRCS)
    img_src = " ".join(ALLOWED_IMG_SRCS)
    font_src = " ".join(ALLOWED_FONT_SRCS)
    connect_src = " ".join(ALLOWED_CONNECT_SRCS)
    frame_src = " ".join(ALLOWED_FRAME_SRCS)
    return (
        f"default-src 'self'; "
        f"script-src {script_src}; "
        f"style-src {style_src}; "
        f"img-src {img_src}; "
        f"font-src {font_src}; "
        f"connect-src {connect_src}; "
        f"worker-src 'self' blob:; "
        f"frame-src {frame_src}; "
        f"child-src {frame_src}; "
        f"object-src 'none'; "
        f"base-uri 'self'; "
        f"frame-ancestors 'self' https://*.mysellauth.com https://sellauth.com;"
    )

def load_json(path: Path) -> List[Dict[str, Any]]:
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            raise ValueError("routes.json must be a list of route objects")
        return data
    except FileNotFoundError:
        logger.warning("JSON file not found: %s", path)
        return []
    except Exception as e:
        logger.error("Error reading %s: %s", path, e)
        return []

@app.before_request
def set_session_and_nonce():
    session.permanent = True
    g.csp_nonce = secrets.token_urlsafe(16)

@app.after_request
def apply_security_headers(resp):
    resp.headers["Content-Security-Policy"] = build_csp(g.get("csp_nonce", ""))
    resp.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "SAMEORIGIN"
    resp.headers["Permissions-Policy"] = "accelerometer=(), camera=(), geolocation=(), gyroscope=(), magnetometer=(), microphone=(), payment=()"
    if FORCE_HTTPS and not DEBUG_MODE:
        resp.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains; preload"
    return resp

@app.context_processor
def inject_nonce():
    return {"csp_nonce": g.get("csp_nonce", "")}

@app.errorhandler(404)
def handle_404(e):
    return render_template("404.html"), 404

@app.errorhandler(429)
def handle_429(e):
    return render_template("429.html", error=str(e.description)), 429

@app.errorhandler(500)
def handle_500(e):
    logger.exception("Server error: %s", e)
    return render_template("500.html"), 500

def register_dynamic_routes() -> None:
    routes = load_json(ROUTES_PATH)
    if not routes:
        logger.warning("No routes loaded from %s", ROUTES_PATH)
        return

    endpoints_seen = set()
    for route in routes:
        try:
            tpl = route["template"].lstrip("/").strip()  # strip leading slash to support your JSON format
            url = route["url"].strip()
            if not url.startswith("/"):
                raise ValueError(f"Invalid url '{url}' (must start with '/')")
            if not (TEMPLATES_DIR / tpl).exists():
                raise FileNotFoundError(f"Template not found: {tpl}")
            endpoint = url.replace("/", "_") or "root"
            if endpoint in endpoints_seen:
                raise ValueError(f"Duplicate endpoint for url: {url}")

            def make_view(template_name: str):
                def _view():
                    return render_template(template_name)
                return _view

            app.add_url_rule(url, endpoint=endpoint, view_func=make_view(tpl))
            endpoints_seen.add(endpoint)
            logger.info("Registered route: %s -> %s", url, tpl)
        except Exception as e:
            logger.error("Error registering route %s: %s", route, e)

@app.get("/_health")
@limiter.exempt
def healthcheck():
    return {"status": "ok"}, 200

@app.get("/")
@limiter.exempt
def home_fallback():
    if (TEMPLATES_DIR / "index.html").exists():
        return render_template("index.html")
    return make_response("<h1>Site online</h1>", 200)

register_dynamic_routes()

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    logger.info("Starting application on port %d (debug=%s, https=%s, proxy_hops=%s)", port, DEBUG_MODE, FORCE_HTTPS, trust_proxy)
    app.run(host="0.0.0.0", port=port, debug=DEBUG_MODE, use_reloader=DEBUG_MODE)
