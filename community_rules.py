"""Pure helpers for community text moderation."""
import re
import unicodedata

DEFAULT_WORDS = ['fuck', 'shit', 'bitch', 'cunt', 'kys', 'kill yourself', 'nigger', 'faggot']
CHANNELS = {'general': 'General', 'questions': 'Questions', 'feedback': 'Feedback', 'off-topic': 'Off topic'}
LEET = str.maketrans({'0':'o','1':'i','3':'e','4':'a','5':'s','7':'t','@':'a','$':'s'})

def normalize(value):
    value = unicodedata.normalize('NFKD', value.casefold())
    return ''.join(c for c in value if unicodedata.category(c) not in {'Mn','Cf','Me'}).translate(LEET)

def blocked(text, words):
    value = normalize(text)
    for word in words:
        parts = re.findall(r'\w+', normalize(word))
        if not parts: continue
        pattern = r'(?<!\w)' + r'[\W_]*'.join(re.escape(c) for c in ''.join(parts)) + r'(?!\w)'
        if re.search(pattern, value): return True
    return False

def valid_text(value, words, limit=1000):
    if not isinstance(value, str): raise ValueError('Enter text.')
    value = value.strip()
    if not value or len(value) > limit: raise ValueError(f'Enter 1–{limit} characters.')
    if any(unicodedata.category(c) in {'Cc','Cs','Cf'} and c not in '\n\t' for c in value):
        raise ValueError('Unsupported characters in text.')
    if blocked(value, words): raise ValueError('Your text contains a restricted word. Please revise it.')
    if re.search(r'(?i)(?:https?\s*:\s*/|www\.|data\s*:|javascript\s*:)', value):
        raise ValueError('External links are not allowed in community posts.')
    return value
