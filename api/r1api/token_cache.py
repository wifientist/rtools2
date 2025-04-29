import time
from threading import Lock

_token_cache = {}
_lock = Lock()

def get_cached_token(tenant_id):
    with _lock:
        entry = _token_cache.get(tenant_id)
        if entry:
            token, expiry = entry
            if expiry is None or expiry > time.time():
                print(f"token found and returned for tenant {tenant_id}")
                return token
    print('No cached token found')
    return None

def store_token(tenant_id, token, expires_in=3600):
    with _lock:
        expiry = time.time() + expires_in - 60  # 1 min safety margin
        _token_cache[tenant_id] = (token, expiry)
        print(f"token cached for tenant {tenant_id}")

