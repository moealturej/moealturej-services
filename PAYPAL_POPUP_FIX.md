# PayPal stuck loading / blank popup fix

This build removes the PayPal Smart Buttons popup flow and uses a normal server-created PayPal approval redirect instead.

Why: if PayPal credentials are wrong or the SDK popup cannot complete `createOrder`, Chrome can leave a white `about:blank` PayPal popup open with a lock spinner. The new flow creates the order first on your Flask server, then redirects the current tab to PayPal's approval link.

Required `.env`:

```env
PAYPAL_MODE=sandbox
PAYPAL_CLIENT_ID=your_sandbox_rest_client_id
PAYPAL_CLIENT_SECRET=your_sandbox_rest_secret
```

For live mode, all three values must be live:

```env
PAYPAL_MODE=live
PAYPAL_CLIENT_ID=your_live_rest_client_id
PAYPAL_CLIENT_SECRET=your_live_rest_secret
```

After changing `.env`, fully stop Flask and start it again.
