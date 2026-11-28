import unittest
from datetime import datetime, timezone
from unittest.mock import patch
from email_guard import rules

class QuotaRules(unittest.TestCase):
    def test_security_shared_by_recipient(self):
        now = datetime(2026,11,27,22,1,tzinfo=timezone.utc)
        with patch.dict('os.environ', {'EMAIL_DAILY_LIMIT':'80','EMAIL_DELIVERY_RESERVE':'15'}):
            a = rules('A@example.org','security','127.0.0.1',now)
            b = rules('a@example.org','security','127.0.0.1',now)
            self.assertEqual(a,b)
            self.assertIn(('global:20261127',80),a)
            self.assertIn(('nonessential:20261127',65),a)
            self.assertEqual(a[0][1],1)
    def test_delivery_reserved(self):
        self.assertFalse(any(k.startswith('nonessential:') for k,v in rules('a@b.com','delivery')))
    def test_broadcast_budget(self):
        self.assertTrue(any(k.startswith('broadcast:') for k,v in rules('a@b.com','broadcast')))
