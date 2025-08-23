import os
import json
import logging
from datetime import timedelta
from functools import wraps
from flask import Flask, session, render_template, abort, request, g
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_compress import Compress
from dotenv import load_dotenv
import html
from werkzeug.middleware.proxy_fix import ProxyFix

# -----------------------------------------------------------------------------
# Initialization
# -----------------------------------------------------------------------------
load_dotenv()

app = Flask(__name__, template_folder="templates", static_folder="static")

# Apply proxy fix if behind a reverse proxy
if os.getenv('BEHIND_PROXY', 'false').lower() == 'true':
    app.wsgi_app = ProxyFix(
        app.wsgi_app, 
        x_for=1, 
        x_proto=1, 
        x_host=1, 
        x_port=1,
        x_prefix=1
    )

# Validate secret key configuration
secret_key = os.getenv('SECRET_KEY')
if not secret_key:
    # Fallback to a temporary key for development with a warning
    if os.getenv('FLASK_ENV') == 'development':
        secret_key = 'dev-key-change-in-production-' + os.urandom(16).hex()
        logging.warning("Using temporary secret key for development. Set SECRET_KEY for production.")
    else:
        raise RuntimeError("SECRET_KEY environment variable is not set")
app.secret_key = secret_key

# Session configuration
app.config.update(
    PERMANENT_SESSION_LIFETIME=timedelta(minutes=20),
    SESSION_COOKIE_SECURE=True,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    SESSION_REFRESH_EACH_REQUEST=True
)

# Security headers configuration
CSP_POLICY = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://cdn.sell.app https://sellauth.com; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdnjs.cloudflare.com; "
    "img-src 'self' https://i.postimg.cc data: https://sellauth.com https:; "
    "font-src 'self' https://fonts.gstatic.com https://cdnjs.cloudflare.com; "
    "connect-src 'self' https://api-internal-2.sellauth.com https://sellauth.com https://formspree.io; "
    "worker-src 'self' blob:; "
    "frame-src https://*.mysellauth.com https://sellauth.com; "
    "object-src 'none';"
    "base-uri 'self';"
    "form-action 'self';"
)

# Initialize compression
Compress(app)

# Rate limiting configuration
limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    default_limits=["5000 per day", "1000 per hour", "100 per minute"],
    storage_uri="memory://",
    strategy="moving-window",
    headers_enabled=True
)

# Configure logging
log_level = os.getenv('LOG_LEVEL', 'INFO').upper()
logging.basicConfig(
    level=log_level,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# Paths to JSON data
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ROUTES_PATH = os.path.join(BASE_DIR, 'static', 'data', 'routes.json')

# Cache for route data
route_cache = None
route_cache_time = 0
CACHE_TIMEOUT = 300  # 5 minutes

# -----------------------------------------------------------------------------
# Helper Functions and Decorators
# -----------------------------------------------------------------------------
def load_json(path):
    """Safely load JSON data from file with caching"""
    global route_cache, route_cache_time
    
    current_time = os.path.getmtime(path) if os.path.exists(path) else 0
    
    # Return cached data if it's still valid
    if route_cache and current_time <= route_cache_time + CACHE_TIMEOUT:
        return route_cache
    
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            route_cache = data
            route_cache_time = current_time
            return data
    except FileNotFoundError:
        logger.error("JSON file not found: %s", path)
        return []
    except json.JSONDecodeError as e:
        logger.error("Error parsing JSON file %s: %s", path, str(e))
        return []
    except Exception as e:
        logger.exception("Unexpected error loading JSON: %s", str(e))
        return []

def rate_limit_exempt(f):
    """Decorator to exempt a route from rate limiting"""
    f._rate_limit_exempt = True
    return f

# -----------------------------------------------------------------------------
# Middleware Handlers
# -----------------------------------------------------------------------------
@app.before_request
def set_session_timeout():
    session.permanent = True

@app.before_request
def check_maintenance():
    """Check if site is in maintenance mode"""
    if os.getenv('MAINTENANCE_MODE', 'false').lower() == 'true':
        if request.path != '/maintenance':
            return render_template('maintenance.html'), 503

@app.after_request
def apply_security_headers(response):
    """Apply security headers to all responses"""
    response.headers["Content-Security-Policy"] = CSP_POLICY
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["X-Download-Options"] = "noopen"
    
    # HSTS header - only in production
    if os.getenv('FLASK_ENV') == 'production' and request.is_secure:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    
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

@app.errorhandler(503)
def service_unavailable(e):
    return render_template("503.html"), 503

# -----------------------------------------------------------------------------
# Dynamic Routes Loader
# -----------------------------------------------------------------------------
def register_dynamic_routes():
    """Register routes from JSON configuration with error handling"""
    routes = load_json(ROUTES_PATH)
    if not routes:
        logger.warning("No routes loaded from configuration")
        # Add a fallback route
        @app.route('/')
        @rate_limit_exempt
        def fallback_home():
            return render_template('index.html')
        return

    for route in routes:
        try:
            tpl = route['template']
            url = route['url']
            methods = route.get('methods', ['GET'])
            
            # Create dedicated view function for each route
            def view_func(template=tpl):
                try:
                    return render_template(template)
                except Exception as e:
                    logger.error("Error rendering template %s: %s", template, str(e))
                    abort(500)
            
            # Set the function name for debugging
            view_func.__name__ = f"route_{url.replace('/', '_').replace('-', '_')}"
            
            # Apply rate limiting unless exempt
            if route.get('rate_limit_exempt', False):
                view_func = rate_limit_exempt(view_func)
            
            app.add_url_rule(
                url, 
                endpoint=url.replace('/', '_').replace('-', '_'), 
                view_func=view_func,
                methods=methods
            )
            logger.info("Registered route: %s -> %s", url, tpl)
        except KeyError as e:
            logger.error("Invalid route configuration: %s. Missing key: %s", route, str(e))
        except Exception as e:
            logger.exception("Error registering route %s: %s", route, str(e))

# Health check endpoint
@app.route('/health')
@rate_limit_exempt
def health_check():
    """Endpoint for health checks"""
    return {'status': 'healthy', 'message': 'Service is running'}

# -----------------------------------------------------------------------------
# Application Startup
# -----------------------------------------------------------------------------
register_dynamic_routes()

if __name__ == '__main__':
    # Production settings
    port = int(os.getenv('PORT', 10000))
    debug = os.getenv('FLASK_ENV', 'development') == 'development'
    
    # Disable debug in production
    if os.getenv('FLASK_ENV') == 'production':
        debug = False
    
    logger.info("Starting application on port %d (debug=%s)", port, debug)
    
    # Use waitress for production instead of Flask's dev server
    if os.getenv('FLASK_ENV') == 'production':
        from waitress import serve
        serve(app, host='0.0.0.0', port=port, threads=8)
    else:
        app.run(
            host='0.0.0.0',
            port=port,
            debug=debug,
            use_reloader=debug
        )
