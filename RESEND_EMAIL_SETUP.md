# Resend Email Setup for moealturej.com

This app now sends all production email through Resend using verified `@moealturej.com` senders.

Gmail must **not** be used as the From address. Gmail is only used for replies/support.

Use these environment variables on Render:

```env
EMAIL_PROVIDER=resend
RESEND_API_KEY=re_your_api_key_here
RESEND_REPLY_TO=moealturej@gmail.com
SUPPORT_EMAIL=moealturej@gmail.com

EMAIL_FROM_EMAIL=security@moealturej.com
SECURITY_FROM_EMAIL=Moealturej Security <security@moealturej.com>
ORDERS_FROM_EMAIL=Moealturej Orders <orders@moealturej.com>
DOWNLOADS_FROM_EMAIL=Moealturej Downloads <downloads@moealturej.com>
SUPPORT_FROM_EMAIL=Moealturej Support <support@moealturej.com>
NOTIFICATIONS_FROM_EMAIL=Moealturej <no-reply@moealturej.com>
REQUIRE_EMAIL_CODES=true
```

The app automatically maps email types like this:

- Security/login/signup/password reset: `security@moealturej.com`
- Orders/receipts/payment emails: `orders@moealturej.com`
- Product key/download delivery: `downloads@moealturej.com`
- Support/ticket emails: `support@moealturej.com`
- General notifications: `no-reply@moealturej.com`

After setting `RESEND_API_KEY`, redeploy Render.
