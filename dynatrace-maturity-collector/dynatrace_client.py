"""Dynatrace Environment and Platform API clients."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

VERSION = "1.0.0"


class DynatraceAPIError(RuntimeError):
    def __init__(self,status,path):
        self.status=status
        self.path=path
        super().__init__(f'HTTP {status}: Dynatrace request failed at {path}')


@dataclass
class ApiClient:
    base_url: str
    token: str
    timeout: int = 30
    retries: int = 3

    authorization_scheme = 'Api-Token'
    path_prefix = '/api/'

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return self.request(path,params)

    def request(self,path,params=None,body=None):
        if not path.startswith(self.path_prefix) or '://' in path:
            raise ValueError('Request path must belong to this Dynatrace API family')
        if body is not None and (self.authorization_scheme!='Bearer' or path!='/platform/storage/query/v1/query:execute'):
            raise ValueError('Only read-only Grail query POST requests are allowed')
        url = self.base_url.rstrip("/") + path
        if params:
            url += "?" + urllib.parse.urlencode(params, doseq=True)
        request = urllib.request.Request(
            url,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Authorization": f"{self.authorization_scheme} {self.token}", "Accept": "application/json", "Content-Type":"application/json", "User-Agent": f"dt-maturity/{VERSION}"},
            method='POST' if body is not None else 'GET',
        )
        for attempt in range(self.retries):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                if exc.code == 429 and attempt + 1 < self.retries:
                    delay=exc.headers.get('Retry-After','2')
                    if delay.isdigit() and int(delay)<=10:
                        time.sleep(int(delay))
                        continue
                raise DynatraceAPIError(exc.code,path) from None
            except urllib.error.URLError as exc:
                if attempt + 1 < self.retries:
                    time.sleep(2**attempt)
                    continue
                raise RuntimeError('Dynatrace connection error') from None
        raise RuntimeError("Request failed")

    def paged(self, path: str, params: dict[str, Any], item_key: str) -> tuple[list[Any], int]:
        items: list[Any] = []
        pages = 0
        next_key: str | None = None
        seen=set()
        while True:
            query = {"nextPageKey": next_key} if next_key else dict(params)
            payload = self.get(path, query)
            if not isinstance(payload,dict) or not isinstance(payload.get(item_key),list):
                raise RuntimeError('Unexpected Dynatrace list response schema')
            pages += 1
            items.extend(payload.get(item_key, []))
            next_key = payload.get("nextPageKey")
            if not next_key:
                return items, pages
            if next_key in seen:raise RuntimeError('Dynatrace pagination repeated nextPageKey')
            seen.add(next_key)


class PlatformClient(ApiClient):
    """Platform Bearer credentials are never sent to Environment API routes."""
    authorization_scheme='Bearer'
    path_prefix='/platform/'

    def documents(self):
        params={'filter':"type='dashboard'",'page-size':1000,'add-fields':'labels'}
        seen=set()
        while True:
            payload=self.get('/platform/document/v1/documents',params)
            if not isinstance(payload.get('documents'),list):raise RuntimeError('Unexpected document list schema')
            yield payload['documents']
            cursor=payload.get('nextPageKey')
            if not cursor:break
            if cursor in seen:raise RuntimeError('Document pagination repeated page-key')
            seen.add(cursor);params['page-key']=cursor

    def query(self,query,start,end):
        prefix=query.lstrip().split(None,1)[0].lower()
        if prefix!='fetch':raise ValueError('Only fetch-based read-only DQL is supported')
        response=self.request('/platform/storage/query/v1/query:execute',body={
            'query':query,'defaultTimeframeStart':start.isoformat(),'defaultTimeframeEnd':end.isoformat(),
            'requestTimeoutMilliseconds':1000})
        # Queries can finish synchronously with a result and no requestToken.
        token=response.get('requestToken')
        for _ in range(20):
            state=response.get('state')
            if state=='SUCCEEDED' and isinstance(response.get('result'),dict):
                result=response['result']
                if not isinstance(result.get('records'),list):raise RuntimeError('Grail result omitted records')
                metadata=result.get('metadata',{}).get('grail',{})
                if metadata.get('notifications'):raise RuntimeError('Grail query returned notifications; completeness requires review')
                return result['records']
            if state in ('FAILED','CANCELLED','RESULT_GONE'):raise RuntimeError('Grail query '+state.lower())
            if not token:raise RuntimeError('Grail query omitted both completed result and requestToken')
            response=self.get('/platform/storage/query/v1/query:poll',{'request-token':token})
            if response.get('state') not in ('SUCCEEDED','FAILED','CANCELLED','RESULT_GONE'):time.sleep(1)
        raise RuntimeError('Grail query did not finish within the polling budget')
