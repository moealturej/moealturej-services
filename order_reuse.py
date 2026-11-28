"""Conservative matching for resuming an existing Stripe checkout."""
import json


def cart_signature(cart):
    def canonical(value):
        return json.dumps(value, sort_keys=True, separators=(',', ':'), default=str)
    items = sorted(canonical(item) for item in cart.get('items', []))
    discounts = sorted(canonical(item) for item in cart.get('discounts', []))
    return canonical([items, discounts, *[cart.get(k) for k in (
        'subtotal_cents', 'discount_cents', 'fee_cents', 'total_cents', 'currency', 'discount_code', 'quantity')]])


def matching_order(order, cart):
    return bool(order and order.get('status') == 'pending' and order.get('provider') == 'stripe'
                and not order.get('stripe_fulfillment_claim') and order.get('provider_session_id')
                and cart_signature(order.get('cart') or {}) == cart_signature(cart))
