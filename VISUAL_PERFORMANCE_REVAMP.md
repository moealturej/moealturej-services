# Visual + Performance Revamp

Updated pages:

- Home
- Store
- Downloads
- Status

## What changed

- Cleaner, more normal dark-purple layout with less oversized/awkward sections.
- Better spacing, cards, page hierarchy, and mobile behavior.
- Store now uses a sidebar-style filter/search layout on desktop and stacks cleanly on mobile.
- Removed the old modal/floating cart behavior from the store page; cart is handled from the navbar/cart page.
- Store product images use lazy loading and async decoding.
- Product rendering is safer with HTML escaping.
- Search inputs use debounced rendering for better performance.
- Store, home, downloads, and status use sessionStorage caching so page switching feels faster.
- Downloads page changed from bulky cards into a cleaner file-list layout.
- Status page changed from small cluttered grid cards into a cleaner live-service list.
- Pages handle empty/error states better.

## Notes

The backend/API routes are unchanged. These are front-end/template upgrades, so your existing MongoDB, owner panel, Stripe, PayPal, Google, Discord, downloads, status, and product data continue working.
