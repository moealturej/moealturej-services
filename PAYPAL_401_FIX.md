# Fix PayPal 401 Unauthorized

A 401 from `/v1/oauth2/token` means PayPal rejected your Client ID/Secret.

## Most common cause

Your app is set to sandbox but you pasted live credentials, or the other way around.

For sandbox testing:

```env
PAYPAL_MODE=sandbox
PAYPAL_CLIENT_ID=your_sandbox_rest_client_id
PAYPAL_CLIENT_SECRET=your_sandbox_rest_secret
```

For live:

```env
PAYPAL_MODE=live
PAYPAL_CLIENT_ID=your_live_rest_client_id
PAYPAL_CLIENT_SECRET=your_live_secret
```

## Where to get the right keys

PayPal Developer Dashboard → Apps & Credentials → REST API app.

Use Sandbox credentials while `PAYPAL_MODE=sandbox`.
Use Live credentials while `PAYPAL_MODE=live`.

## After changing .env

Fully stop Flask and run it again:

```bash
python app.py
```

Do not just refresh the browser; environment variables are loaded when the app starts.
