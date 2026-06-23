# Pending Order Auto-Delete Patch

Pending unpaid orders are now automatically removed after 10 minutes.

## What changed

- Added `PENDING_ORDER_MAX_AGE_MINUTES`, defaulting to `10`.
- New pending Stripe and PayPal orders save an `expires_at` timestamp.
- A lightweight cleanup runs on normal page/API requests, so abandoned pending orders disappear from user and admin order panels without needing a separate worker.
- Stale pending Stripe checkout sessions are expired best-effort before the local order is deleted.
- Paid orders are never deleted by this cleanup.
- Missing/deleted orders are no longer recreated as empty orders by late payment callbacks.

## Optional env

```env
PENDING_ORDER_MAX_AGE_MINUTES=10
```
