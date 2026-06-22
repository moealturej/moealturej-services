# Manual key delivery setup

This build supports manual key delivery after a successful Stripe or PayPal payment.

## What happens after a purchase

1. Customer pays with Stripe or PayPal.
2. The order status becomes `paid`.
3. The owner Discord webhook is notified with the order, buyer, total, and items waiting for a key.
4. Owner opens `/admin#orders`.
5. Owner pastes the product key for the purchased item and clicks **Send key**.
6. The key is saved to the customer's `/account` order history.
7. The customer gets a branded email with the key.
8. If the customer connected Discord and your bot token is configured, the customer also gets a branded Discord DM.

## Required `.env`

```env
OWNER_ORDER_WEBHOOK_URL=https://discord.com/api/webhooks/your-webhook
DELIVERY_DM_ENABLED=true
DISCORD_BOT_TOKEN=your_bot_token
EMAIL_FROM_EMAIL=moealturej@gmail.com
RESEND_API_KEY=re_your_resend_api_key
```

## Security note

Discord webhook URLs are secrets. If a webhook URL was posted publicly or committed to GitHub, regenerate it in Discord and update `.env`.

## Discord DM notes

The DM only sends when:

- `DISCORD_BOT_TOKEN` is valid
- the customer has linked their Discord account on the site
- `DELIVERY_DM_ENABLED=true`
- the customer's Discord privacy settings allow DMs from your bot/server

If the DM fails, the key still saves to the account and the email can still send.


## Email delivery on Render

This build uses Resend first. See `RESEND_EMAIL_SETUP.md` and set `RESEND_API_KEY` in Render. SMTP is only a fallback for local development.
