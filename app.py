import os
import json
import logging
from datetime import timedelta
from flask import Flask, session, render_template, abort
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from dotenv import load_dotenv

# -----------------------------------------------------------------------------
# Initialization
# -----------------------------------------------------------------------------
load_dotenv()

app = Flask(__name__, template_folder="templates", static_folder="static")

# Validate secret key configuration
secret_key = os.getenv('SECRET_KEY')
if not secret_key:
    raise RuntimeError("SECRET_KEY environment variable is not set")
app.secret_key = secret_key

# Session configuration
app.config.update(
    PERMANENT_SESSION_LIFETIME=timedelta(minutes=20),
    SESSION_COOKIE_SECURE=True,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax'
)

# Security headers configuration
CSP_POLICY = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://cdn.sell.app https://sellauth.com; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdnjs.cloudflare.com; "
    "img-src 'self' https://i.postimg.cc data: https://sellauth.com; "
    "font-src 'self' https://fonts.gstatic.com https://cdnjs.cloudflare.com; "
    "connect-src 'self' https://api-internal-2.sellauth.com https://sellauth.com https://formspree.io; "
    "worker-src 'self' blob:; "
    "frame-src https://*.mysellauth.com https://sellauth.com; "
    "object-src 'none';"
)

# Rate limiting configuration
limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    default_limits=["200 per day", "50 per hour"],
    storage_uri="memory://",
    strategy="fixed-window"
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# Paths to JSON data
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ROUTES_PATH = os.path.join(BASE_DIR, 'static', 'data', 'routes.json')

# -----------------------------------------------------------------------------
# Helper Functions
# -----------------------------------------------------------------------------
def load_json(path):
    """Safely load JSON data from file"""
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        logger.error("JSON file not found: %s", path)
        return []
    except json.JSONDecodeError as e:
        logger.error("Error parsing JSON file %s: %s", path, str(e))
        return []

# -----------------------------------------------------------------------------
# Middleware Handlers
# -----------------------------------------------------------------------------
@app.before_request
def set_session_timeout():
    session.permanent = True

@app.after_request
def apply_security_headers(response):
    """Apply security headers to all responses"""
    response.headers["Content-Security-Policy"] = CSP_POLICY
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response

# -----------------------------------------------------------------------------
# Error Handlers
# -----------------------------------------------------------------------------
@app.errorhandler(404)
def page_not_found(e):
    return render_template("404.html"), 404

@app.errorhandler(429)
def ratelimit_handler(e):
    return render_template("429.html", error=str(e.description)), 429

@app.errorhandler(500)
def internal_error(e):
    logger.error("Server error: %s", str(e))
    return render_template("500.html"), 500

# -----------------------------------------------------------------------------
# Dynamic Routes Loader
# -----------------------------------------------------------------------------
def register_dynamic_routes():
    """Register routes from JSON configuration"""
    routes = load_json(ROUTES_PATH)
    if not routes:
        logger.warning("No routes loaded from configuration")
        return

    for route in routes:
        try:
            tpl = route['template']
            url = route['url']
            
            # Create dedicated view function for each route
            def view_func(template=tpl):
                return render_template(template)
                
            app.add_url_rule(
                url, 
                endpoint=url.replace('/', '_'), 
                view_func=view_func
            )
            logger.info("Registered route: %s -> %s", url, tpl)
        except KeyError as e:
            logger.error("Invalid route configuration: %s. Missing key: %s", route, str(e))
        except Exception as e:
            logger.exception("Error registering route %s: %s", route, str(e))

# -----------------------------------------------------------------------------
# Application Startup
# -----------------------------------------------------------------------------
register_dynamic_routes()

if __name__ == '__main__':
    # Production settings
    port = int(os.getenv('PORT', 10000))
    debug = os.getenv('DEBUG_MODE', 'false').lower() == 'true'
    
    logger.info("Starting application on port %d (debug=%s)", port, debug)
    app.run(
        host='0.0.0.0',
        port=port,
        debug=debug,
        use_reloader=False if not debug else True
    )