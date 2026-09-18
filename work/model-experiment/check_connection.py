"""Unauthenticated diagnostic: does not load or print the API key."""
import urllib.request
import urllib.error

print('Proxy schemes:', ','.join(urllib.request.getproxies().keys()) or 'none')
try:
    with urllib.request.urlopen('https://api.deepseek.com', timeout=15) as r:
        print('HTTP status:', r.status)
except urllib.error.HTTPError as e:
    print('Server reachable. HTTP status:', e.code)
except urllib.error.URLError as e:
    print('Connection error:', type(e.reason).__name__, str(e.reason))
