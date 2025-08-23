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


# ----------------------------------------------------------------------------
# Initialization & Config
# ----------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"
ROUTES_PATH = STATIC_DIR / "data" / "routes.json"


app = Flask(__name__, template_folder=str(TEMPLATES_DIR), static_folder=str(STATIC_DIR))


# Secret key (required)
secret_key = os.getenv("SECRET_KEY")
if not secret_key:
raise RuntimeError("SECRET_KEY environment variable is not set")
app.secret_key = secret_key


# Reverse proxy awareness (very important if behind nginx / cloud)
trust_proxy = int(os.getenv("TRUST_PROXY", "0"))
if trust_proxy > 0:
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=trust_proxy, x_proto=trust_proxy, x_host=trust_proxy, x_port=trust_proxy, x_prefix=trust_proxy)


# Runtime toggles
DEBUG_MODE = os.getenv("DEBUG_MODE", "false").lower() == "true"
FORCE_HTTPS = os.getenv("FORCE_HTTPS", "true").lower() == "true"


# Session & cookie policy (cross‑browser compatible)
# Safari requires SameSite=None + Secure for third‑party / embedded contexts.
SESSION_SAMESITE = os.getenv("SESSION_SAMESITE", "None") # 'None' | 'Lax' | 'Strict'
SESSION_SECURE = os.getenv("SESSION_SECURE", "true").lower() == "true"


app.config.update(
PERMANENT_SESSION_LIFETIME=timedelta(minutes=30),
SESSION_COOKIE_SECURE=SESSION_SECURE and FORCE_HTTPS and not DEBUG_MODE,
SESSION_COOKIE_HTTPONLY=True,
SESSION_COOKIE_SAMESITE=SESSION_SAMESITE,
)


# ----------------------------------------------------------------------------
# Logging (structured + console)
# ----------------------------------------------------------------------------
logger = logging.getLogger("app")
logger.setLevel(logging.INFO)
handler = logging.StreamHandler()
formatter = logging.Formatter('%(asctime)s %(levelname)s %(name)s %(message)s')
handler.setFormatter(formatter)
if not logger.handlers:
logger.addHandler(handler)
app.run(host="0.0.0.0", port=port, debug=DEBUG_MODE, use_reloader=DEBUG_MODE)
