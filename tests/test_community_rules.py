import unittest
from community_rules import blocked, valid_text, DEFAULT_WORDS

class ModerationTests(unittest.TestCase):
    def test_normal_and_boundary(self):
        self.assertEqual(valid_text('  Hello community!  ', DEFAULT_WORDS), 'Hello community!')
        self.assertFalse(blocked('class assignment', ['ass']))
    def test_case_substitution_and_spacing(self):
        for value in ['SHIT', 'sh1t', 's.h.i.t', 's h i t', 'shít', 'sh\u200bit', 'kill yourself']:
            self.assertTrue(blocked(value, DEFAULT_WORDS), value)
    def test_invalid(self):
        for value in ['', 'x'*1001, 'https://example.org', 'www.example.org', 'x\x00y']:
            with self.assertRaises(ValueError): valid_text(value, DEFAULT_WORDS)
    def test_custom(self):
        self.assertTrue(blocked('banned phrase', ['Banned Phrase']))
        self.assertFalse(blocked('ordinary message', ['banned phrase']))
if __name__ == '__main__': unittest.main()
