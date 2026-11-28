import unittest
from copy import deepcopy
from order_reuse import matching_order

class OrderReuseTests(unittest.TestCase):
    def setUp(self):
        self.cart = {'items': [{'slug': 'a', 'quantity': 1, 'unit_cents': 100}, {'slug': 'b', 'quantity': 2, 'unit_cents': 200}], 'total_cents': 500, 'currency': 'USD', 'discount_code': ''}
        self.order = {'cart': deepcopy(self.cart), 'status': 'pending', 'provider': 'stripe', 'provider_session_id': 'cs_test'}
    def test_same_cart_reordered(self):
        self.cart['items'].reverse()
        self.assertTrue(matching_order(self.order, self.cart))
    def test_changed_cart(self):
        self.cart['items'][0]['quantity'] = 2
        self.assertFalse(matching_order(self.order, self.cart))
    def test_changed_price_or_discount(self):
        self.cart['total_cents'] = 600
        self.assertFalse(matching_order(self.order, self.cart))
        self.cart['total_cents'] = 500
        self.cart['discount_code'] = 'NEW'
        self.assertFalse(matching_order(self.order, self.cart))
    def test_ineligible_orders(self):
        for key, value in [('status', 'paid'), ('provider', 'paypal'), ('provider_session_id', ''), ('stripe_fulfillment_claim', 'claimed')]:
            self.assertFalse(matching_order(dict(self.order, **{key: value}), self.cart))
