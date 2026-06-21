# Production readiness notes

This build is prepared for public deployment with signed-in-only checkout, Stripe/PayPal server-side payment creation, owner dashboard controls, upload safety checks, CSRF protection, rate limiting, security headers, and MongoDB-first persistence with local JSON fallback for dev.

## Required production env

```env
FLASK_ENV=production
SECRET_KEY=use-a-long-random-secret
BEHIND_PROXY=true
APP_NAME=moealturej
APP_URL=https://moealturej.com

MONGO_URI=mongodb+srv://USER:PASSWORD@cluster0.xxxxx.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0
MONGO_DB_NAME=moealturej_services

OWNER_EMAIL=moealturej@gmail.com
OWNER_USERNAME=moealturej
OWNER_PASSWORD=strong-owner-password

SMTP_EMAIL=moealturej@gmail.com
SMTP_PASSWORD=google-app-password
REQUIRE_EMAIL_CODES=true

DISCORD_CLIENT_ID=...
DISCORD_CLIENT_SECRET=...
DISCORD_REDIRECT_URI=https://moealturej.com/auth/discord/callback
DISCORD_BOT_TOKEN=optional-for-server-join
DISCORD_AUTO_JOIN_GUILD=true
DISCORD_GUILD_ID=1224469092606410762

STRIPE_SECRET_KEY=sk_live_or_test
STRIPE_WEBHOOK_SECRET=whsec_...
STRIPE_CURRENCY=usd

PAYPAL_MODE=live
PAYPAL_CLIENT_ID=live_client_id
PAYPAL_CLIENT_SECRET=live_secret

PROCESSING_FEE_PERCENT=10
CHECKOUT_REQUIRE_LOGIN=true
RATELIMIT_STORAGE_URI=redis://your-redis-url
```

## Important

- Use Redis for rate limiting in production. `memory://` is okay only for local testing.
- In Stripe, add the webhook endpoint: `https://yourdomain.com/webhooks/stripe` and enable `checkout.session.completed`.
- In PayPal, use REST API credentials. Sandbox credentials only work with `PAYPAL_MODE=sandbox`; Live credentials only work with `PAYPAL_MODE=live`.
- In Discord Developer Portal, add the exact redirect URL used by `DISCORD_REDIRECT_URI`.
- The owner dashboard now has Owner Site Controls for maintenance mode, signup/store toggles, announcements, and diagnostics.

## Running

Local:

```bash
pip install -r requirements.txt
python app.py
```

Production:

```bash
gunicorn wsgi:app --bind 0.0.0.0:$PORT --workers 2 --threads 4 --timeout 120
```
