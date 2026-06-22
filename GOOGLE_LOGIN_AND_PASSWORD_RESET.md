# Google Login + Forgot Password Setup

## Google OAuth

Create an OAuth client in Google Cloud Console:

1. Go to Google Cloud Console > APIs & Services > Credentials.
2. Create an OAuth client ID.
3. Select Web application.
4. Add this local redirect URI:

```text
http://127.0.0.1:10000/auth/google/callback
```

For production, add:

```text
https://yourdomain.com/auth/google/callback
```

Then add the values to `.env`:

```env
GOOGLE_CLIENT_ID=your_google_client_id
GOOGLE_CLIENT_SECRET=your_google_client_secret
GOOGLE_REDIRECT_URI=http://127.0.0.1:10000/auth/google/callback
```

Google login requires MongoDB for normal customer accounts because accounts are saved to the users collection.

## Forgot password

Forgot password uses MongoDB and your existing Resend email delivery app password.

Required values:

```env
EMAIL_FROM_EMAIL=moealturej@gmail.com
RESEND_API_KEY=re_your_resend_api_key
EMAIL_PROVIDER=resend
EMAIL_FROM_NAME=moealturej Security
```

The reset link expires in 20 minutes and stores only a hashed reset token in MongoDB.


## Email delivery on Render

This build uses Resend first. See `RESEND_EMAIL_SETUP.md` and set `RESEND_API_KEY` in Render. SMTP is only a fallback for local development.
