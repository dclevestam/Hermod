"""GOA OAuth2 token acquisition helpers."""

import time as _time


def get_goa_access_token(goa_obj, account, network_ready_fn=None, retries=3, wait=10):
    """Get a GOA OAuth2 token, retrying on transient network/bootstrap failures."""
    network_ready_fn = network_ready_fn or (lambda: True)
    for attempt in range(retries):
        if not network_ready_fn():
            if attempt + 1 < retries:
                delay = min(2, max(1, wait * attempt if attempt else 1))
                print(f'GOA preflight waiting for network... retrying in {delay}s')
                _time.sleep(delay)
                continue
            raise RuntimeError('network not ready')
        try:
            account.call_ensure_credentials_sync(None)
            return goa_obj.get_oauth2_based().call_get_access_token_sync(None)[0]
        except Exception as e:
            text = str(e).lower()
            transient = (
                'status 0' in text
                or '((null))' in text
                or 'expected status 200 when requesting access token' in text
                or 'network not ready' in text
            )
            if attempt + 1 < retries and transient:
                delay = wait * (attempt + 1)
                print(f'Token fetch failed, retrying in {delay}s... ({e})')
                _time.sleep(delay)
            else:
                raise
    raise RuntimeError('unreachable')
