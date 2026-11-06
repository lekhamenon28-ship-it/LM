"""Datadog-only authentication, HTTP requests and pagination."""
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request


def dd_client():
    site=os.environ.get('DD_SITE','datadoghq.com').strip()
    base=site if '://' in site else 'https://api.'+site
    def required(name):
        value=os.environ.get(name,'').strip()
        if not value:raise RuntimeError('Missing environment variable '+name)
        return value
    return base.rstrip('/'), {'DD-API-KEY':required('DD_API_KEY'),'DD-APPLICATION-KEY':required('DD_APP_KEY'),'Accept':'application/json'}

class APIError(RuntimeError):
    def __init__(self, status, path, message):
        self.status, self.path = status, path
        super().__init__(f'HTTP {status}: {message}')

class Client:
    def __init__(self):
        self.base, self.headers = dd_client()
        self.blocked = {}

    def request(self, path, params=None, body=None, timeout=30):
        # Pagination links must never send credentials to a different origin.
        if path.startswith(('https://', 'http://')):
            parsed, expected = urllib.parse.urlsplit(path), urllib.parse.urlsplit(self.base)
            if (parsed.scheme, parsed.netloc) != (expected.scheme, expected.netloc):
                raise RuntimeError('Rejected pagination link outside configured Datadog API origin')
            path = parsed.path + ('?' + parsed.query if parsed.query else '')
        route = re.sub(r'/metrics/[^/]+', '/metrics/{name}', path.split('?')[0])
        if route in self.blocked:
            raise self.blocked[route]
        url = self.base + path
        if params:
            url += ('&' if '?' in url else '?') + urllib.parse.urlencode(params)
        if body is not None and path.split('?')[0] not in ('/api/v2/rum/analytics/aggregate', '/api/v2/events/search', '/api/v2/spans/analytics/aggregate'):
            raise RuntimeError('POST allowed only for read-only analytics endpoints')
        data = json.dumps(body).encode() if body is not None else None
        for attempt in range(3):
            try:
                req = urllib.request.Request(url, data=data, headers={**self.headers, 'Content-Type': 'application/json'}, method='POST' if body is not None else 'GET')
                with urllib.request.urlopen(req, timeout=timeout) as response:
                    return json.load(response)
            except urllib.error.HTTPError as exc:
                if exc.code == 429 and attempt < 2:
                    delay = exc.headers.get('Retry-After', '2')
                    wait = int(delay) if delay.isdigit() else 2
                    if wait <= 10:
                        time.sleep(wait)
                        continue
                # Do not persist request headers, query credentials or raw response bodies.
                err = APIError(exc.code, path.split('?')[0], {401:'authentication_rejected',403:'permission_or_entitlement_denied',404:'not_found_or_unavailable',429:'rate_limited'}.get(exc.code,'request_failed'))
                if exc.code in (401,403,429):
                    self.blocked[route] = err
                raise err from None
            except urllib.error.URLError:
                if attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                raise RuntimeError('network_error') from None

    def offset(self, path, key='data', size=100, size_key='page[limit]', offset_key='page[offset]', params=None, numbered=False):
        """Yield pages; detect ignored offsets instead of silently truncating or looping."""
        offset, seen = 0, set()
        while True:
            payload = self.request(path, {**(params or {}), size_key:size, offset_key:offset})
            page = payload if key is None else payload.get(key, [])
            if not isinstance(page, list):
                raise RuntimeError('Unexpected list response schema')
            signature = json.dumps(page, sort_keys=True)
            if page and signature in seen:
                raise RuntimeError('Pagination repeated a page; partial coverage')
            seen.add(signature)
            yield payload
            if not page:
                break
            pagination = (payload.get('meta', {}).get('pagination') or {}) if isinstance(payload,dict) else {}
            next_offset = pagination.get('next_offset')
            if next_offset is not None:
                if int(next_offset)==0 and len(page)<size:
                    break
                if int(next_offset) <= offset:
                    raise RuntimeError('Pagination offset did not advance')
                offset = int(next_offset)
            elif len(page) < size:
                break
            else:
                offset += 1 if numbered else len(page)
