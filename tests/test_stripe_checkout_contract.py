"""Offline regression checks for the Checkout Session API contract."""
import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class CheckoutContractTests(unittest.TestCase):
    def test_session_options(self):
        tree = ast.parse((ROOT / 'app.py').read_text(encoding='utf-8'))
        assignments = [n for n in ast.walk(tree) if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'checkout_options' for t in n.targets)]
        self.assertEqual(len(assignments), 1)
        options = compile(ast.Expression(assignments[0].value), '<checkout options>', 'eval')
        scope = {'return_url': 'https://example.com/return?session_id={CHECKOUT_SESSION_ID}', 'APP_URL': 'https://example.com/', 'url_for': lambda _: '/checkout'}
        embedded = eval(options, dict(scope, embedded=True))
        self.assertEqual(embedded['ui_mode'], 'embedded_page')
        self.assertEqual(embedded['allowed_payment_method_types'], ['card'])
        self.assertNotIn('payment_method_types', embedded)
        self.assertNotIn('success_url', embedded)
        self.assertEqual(embedded['return_url'], scope['return_url'])
        hosted = eval(options, dict(scope, embedded=False))
        self.assertEqual(hosted['success_url'], scope['return_url'])
        self.assertEqual(hosted['cancel_url'], 'https://example.com/checkout')
        self.assertNotIn('return_url', hosted)

    def test_frontend_initializer_and_cache(self):
        js = (ROOT / 'static/js/pages/checkout.js').read_text(encoding='utf-8')
        self.assertIn('stripe.createEmbeddedCheckoutPage || stripe.initEmbeddedCheckout', js)
        self.assertIn('initialize.call(stripe, {fetchClientSecret:', js)
        self.assertIn('20261128-stripe3', (ROOT / 'templates/checkout.html').read_text(encoding='utf-8'))
