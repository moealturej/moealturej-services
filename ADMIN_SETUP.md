# moealturej Services — Admin / Mongo / 2FA Setup

## What this upgrade adds

- Owner/admin dashboard at `/admin` and `/owner`
- MongoDB-backed products, users, uploaded media records, and audit logs
- Account control: view users, change roles, suspend/unsuspend accounts, reset passwords, and delete accounts
- Product control: add products, remove products, edit full product JSON, update downloads, and update live status
- Upload manager for product images and download files
- Email verification codes for login and signup using Resend email delivery
- CSRF protection for POST forms
- Upload safety: file size limit, extension allow-list, randomized file names, and safe serving routes
- Cleaner premium dark/purple admin visuals
- Local JSON fallback for products and media when MongoDB is not configured

## Basic setup

1. Copy `.env.example` to `.env`.
2. Set a real `SECRET_KEY`.
3. Add your MongoDB connection string as `MONGO_URI`.
4. Set your owner login:

```env
OWNER_EMAIL=your@email.com
OWNER_USERNAME=moealturej
OWNER_PASSWORD=your-strong-password
```

5. Install dependencies:

```bash
pip install -r requirements.txt
```

6. Run:

```bash
python app.py
```

7. Open:

```text
http://127.0.0.1:10000/admin
```

## Resend 2FA / email codes

The app sends 6-digit login/signup codes, password resets, and key delivery emails through Resend when this is enabled:

```env
EMAIL_FROM_EMAIL=security@moealturej.com
RESEND_API_KEY=re_your_resend_api_key
EMAIL_PROVIDER=resend
EMAIL_FROM_NAME=moealturej Security
REQUIRE_EMAIL_CODES=true
```

Important: on Render, use `RESEND_API_KEY`. SMTP is only an optional local fallback.

In development, if no email provider is configured, the app can show security codes as a flash message so you can test. In production, missing Resend settings block login/signup codes for safety.

## Upload manager

Admin uploads are split into two types:

- **Image / product art** → saved under `/media/<file>` and can be pasted into a product `image` field.
- **Download file** → saved under `/download/<file>`, but it only becomes downloadable after you paste that URL into a product `downloads.downloadUrl` field. This prevents random uploaded files from being publicly downloadable by accident.

Allowed image types: `png`, `jpg`, `jpeg`, `gif`, `webp`.

Allowed download types: `zip`, `rar`, `7z`, `pdf`, `txt`, `json`, `png`, `jpg`, `jpeg`, `gif`, `webp`.

Default max upload size is 30 MB. Change it with:

```env
MAX_UPLOAD_MB=30
```

## Admin roles

- `owner`: full access. The `OWNER_EMAIL` account cannot be demoted or deleted.
- `admin`: dashboard access for managing the site.
- `user`: normal customer account.

Only the main owner can promote another account to owner.

## Render notes

Add these environment variables in Render:

- `FLASK_ENV=production`
- `SECRET_KEY`
- `MONGO_URI`
- `MONGO_DB_NAME`
- `OWNER_EMAIL`
- `OWNER_USERNAME`
- `OWNER_PASSWORD`
- `EMAIL_FROM_EMAIL=security@moealturej.com`
- `RESEND_API_KEY`
- `EMAIL_PROVIDER=resend`
- `EMAIL_FROM_NAME=moealturej Security`
- `REQUIRE_EMAIL_CODES=true`
- `BEHIND_PROXY=true`

Use this start command:

```bash
python app.py
```

## Security checklist before public launch

- Change `OWNER_PASSWORD`.
- Use a long random `SECRET_KEY`.
- Use MongoDB Atlas with a strong DB password.
- Set `FLASK_ENV=production`.
- Keep `REQUIRE_EMAIL_CODES=true`.
- Use Resend for production email delivery on Render.
- Do not allow public write access to MongoDB.
- Do not upload raw `.exe`, `.bat`, `.cmd`, `.html`, or `.js` files.

## Discord login / signup

This version adds a Discord OAuth button on login and signup. The routes are rate-limited and protected with OAuth state tokens.

Add these to `.env`:

```env
DISCORD_CLIENT_ID=your-discord-application-client-id
DISCORD_CLIENT_SECRET=your-discord-application-client-secret
DISCORD_REDIRECT_URI=http://127.0.0.1:10000/auth/discord/callback
```

For production, change the redirect URI to your live domain:

```env
DISCORD_REDIRECT_URI=https://moealturej.com/auth/discord/callback
```

In the Discord Developer Portal, add the exact same redirect URI under **OAuth2 → Redirects**. If it does not match exactly, Discord will reject the login.

Normal Discord accounts require MongoDB so the site can save/link the account. In local JSON fallback mode, only the owner can use Discord login when the Discord email matches `OWNER_EMAIL`.

## MongoDB error in your log

This error:

```text
DNS query name does not exist: _mongodb._tcp.cluster.mongodb.net
```

means your `.env` still has the placeholder hostname:

```env
MONGO_URI=mongodb+srv://USERNAME:PASSWORD@cluster.mongodb.net/?retryWrites=true&w=majority
```

Replace it with the real MongoDB Atlas connection string. It should look more like:

```env
MONGO_URI=mongodb+srv://USERNAME:PASSWORD@cluster0.xxxxx.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0
```

The updated app now detects obvious placeholder Mongo URIs and falls back quietly instead of dumping a giant traceback.

## Discord bot token

Basic Discord login does **not** need a bot token. The login button uses Discord OAuth with:

- `DISCORD_CLIENT_ID`
- `DISCORD_CLIENT_SECRET`
- `DISCORD_REDIRECT_URI`

A bot token is only needed later if you want extra Discord features, for example:

- Check if the logged-in Discord account is inside your server
- Check if they have a certain role
- Give or remove Discord roles
- Send DMs from your bot
- Sync website accounts to Discord server accounts

For now, leave `DISCORD_BOT_TOKEN` blank unless you are adding one of those features.

## Branded automatic emails

The login/signup security-code emails now use a branded dark purple HTML template. You can control the email branding with:

```env
APP_NAME=moealturej
APP_URL=https://moealturej.com
SUPPORT_EMAIL=moealturej@gmail.com
BRAND_LOGO_URL=https://moealturej.com/static/logo.png
```

`BRAND_LOGO_URL` must be a public HTTPS URL for the logo to show in most email inboxes. If you leave it blank, the email uses a clean generated `m` logo badge.
## Discord OAuth note

For website Discord login, the site uses OAuth2 and needs `DISCORD_CLIENT_ID` plus `DISCORD_CLIENT_SECRET`. `DISCORD_BOT_TOKEN` is optional and is only used later for bot/server actions like checking guild membership, checking roles, giving roles, or bot DMs. If the login button says Discord is not configured but your bot token is set, add the OAuth2 Client Secret from the Discord Developer Portal.

## Discord auto-join test server

This build can add the logged-in Discord user to your test server after OAuth login.

Add this to `.env`:

```env
DISCORD_BOT_TOKEN=your-bot-token
DISCORD_AUTO_JOIN_GUILD=true
DISCORD_GUILD_ID=1224469092606410762
```

Important Discord rules:

- The bot must already be invited to that server.
- The OAuth login must request `guilds.join`. This build automatically adds that scope when `DISCORD_BOT_TOKEN` and `DISCORD_GUILD_ID` are set.
- If you already logged in before adding this, log out and click Discord login again so Discord asks for the new permission.
- The bot token does not replace `DISCORD_CLIENT_SECRET`; OAuth login still needs Client ID + Client Secret.

If auto-join fails, the login still succeeds, but the dashboard will show a warning flash explaining what Discord rejected.


## Email delivery on Render

This build uses Resend first. See `RESEND_EMAIL_SETUP.md` and set `RESEND_API_KEY` in Render. SMTP is only a fallback for local development.
