"""Persistent, fail-closed outbound email attempt budgets (UTC fixed windows)."""
import hashlib
import os
from datetime import datetime, timezone, timedelta


def setting(name, default):
    try:
        return max(0, int(os.getenv(name, str(default))))
    except ValueError:
        return default


def rules(recipient, category, ip=None, now=None):
    now = now or datetime.now(timezone.utc)
    day = now.strftime('%Y%m%d')
    hour = now.strftime('%Y%m%d%H')
    identity = hashlib.sha256(recipient.strip().lower().encode()).hexdigest()
    daily = setting('EMAIL_DAILY_LIMIT', 80)
    reserve = min(daily, setting('EMAIL_DELIVERY_RESERVE', 15))
    security = category == 'security'
    result = [(f'recipient:{identity}:{category}:{day}', setting('EMAIL_SECURITY_RECIPIENT_DAILY' if security else 'EMAIL_RECIPIENT_DAILY', 5 if security else 12))]
    if security:
        result.insert(0, (f'recipient:{identity}:security:{now.strftime("%Y%m%d%H%M")}', 1))
        result.append((f'recipient:{identity}:security:{hour}', setting('EMAIL_SECURITY_RECIPIENT_HOURLY', 3)))
        if ip:
            digest = hashlib.sha256(ip.encode()).hexdigest()
            result.append((f'ip:{digest}:{hour}', setting('EMAIL_SECURITY_IP_HOURLY', 8)))
    if category not in ('delivery', 'order'):
        result.append((f'nonessential:{day}', daily-reserve))
    if category == 'broadcast':
        result.append((f'broadcast:{day}', setting('EMAIL_BROADCAST_DAILY', 10)))
    result.append((f'global:{day}', daily))
    return result


def reserve_attempt(collection, recipient, category, ip=None):
    """Atomic counters across workers; partial reservations are conservatively retained."""
    if collection is None:
        return False
    from pymongo.errors import DuplicateKeyError
    now = datetime.now(timezone.utc)
    for key, limit in rules(recipient, category, ip, now):
        if limit <= 0:
            return False
        try:
            result = collection.update_one({'_id': key, 'count': {'$lt': limit}},
                {'$inc': {'count': 1}, '$setOnInsert': {'expires_at': now + timedelta(days=3)}}, upsert=True)
        except DuplicateKeyError:
            return False
        if not (result.modified_count or result.upserted_id):
            return False
    return True
