# Stripe + PayPal Checkout Setup

This build removes the SellAuth embed and uses your own checkout routes. Purchases are signed-in-only.

## Stripe

Add to `.env`:

```env
STRIPE_SECRET_KEY=sk_test_or_live_key
STRIPE_WEBHOOK_SECRET=whsec_optional_but_recommended
STRIPE_CURRENCY=usd
```

Stripe checkout starts at `/checkout/stripe`. The webhook endpoint is:

```text
/webhooks/stripe
```

For production, add the full URL in Stripe, for example:

```text
https://moealturej.com/webhooks/stripe
```

Listen for `checkout.session.completed`.

## PayPal

Add to `.env`:

```env
PAYPAL_MODE=sandbox
PAYPAL_CLIENT_ID=your_client_id
PAYPAL_CLIENT_SECRET=your_secret
```

Use `PAYPAL_MODE=live` after your live REST app is ready.

## Fees

`PROCESSING_FEE_PERCENT=10` adds a 10% processing fee based on the cart subtotal.

## Signed-in-only purchases

Cart can be built while browsing, but `/checkout` and payment creation require login.
