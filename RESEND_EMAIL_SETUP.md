# Resend email setup for Render

This build uses **Resend first** for every automatic email:

- Login 2FA/security codes
- Signup verification codes
- Forgot-password reset links
- Product key delivery emails
- Any future branded HTML emails

Resend is better for Render because it sends through an HTTPS API call instead of SMTP ports.

## 1. Add these environment variables

```env
EMAIL_PROVIDER=resend
RESEND_API_KEY=re_your_resend_api_key
EMAIL_FROM_EMAIL=moealturej@gmail.com
EMAIL_FROM_NAME=moealturej Security
SUPPORT_EMAIL=moealturej@gmail.com
REQUIRE_EMAIL_CODES=true
```

`moealturej@gmail.com` is used as the sender address in the app config. In Resend, your sender must be allowed/verified by Resend. For full production delivery, Resend usually works best with a verified domain email like `support@moealturej.com`. If Resend rejects Gmail as a sender, verify your domain in Resend and set:

```env
EMAIL_FROM_EMAIL=support@moealturej.com
SUPPORT_EMAIL=moealturej@gmail.com
```

That keeps customer support replies going to Gmail while sending from a production-ready verified domain.

## 2. Remove Gmail SMTP reliance on Render

You can leave SMTP variables blank:

```env
SMTP_PASSWORD=
```

SMTP is now only a fallback for local development.

## 3. Restart the app

After adding `RESEND_API_KEY`, restart Render/the Flask process. The admin dashboard setup status should show Resend as ready.

## 4. Test

Test these once after deployment:

- Signup verification email
- Login security code
- Forgot password email
- Owner key delivery email

If an email fails, check Render logs. Resend errors are now logged with the HTTP status and response text.
