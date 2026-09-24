"""Synthetic in-memory SDK transport for local entry checks only."""
from backup_test_support import CLOUD, WORKER, OPS
from alibabacloud_oss_v2.types import HttpClient, HttpResponse
from requests.structures import CaseInsensitiveDict
from urllib.parse import urlsplit, parse_qs
class Response(HttpResponse):
    def __init__(self, request, spec):
        self._request = request
        self.spec = spec
        self._headers = CaseInsensitiveDict(spec["headers"])
        self.closed = False
        self.consumed = False
        self.bytes_yielded = 0

    request = property(lambda self: self._request)
    status_code = property(lambda self: self.spec.get("status", 200))
    headers = property(lambda self: self._headers)
    reason = property(lambda self: "Synthetic response")
    is_closed = property(lambda self: self.closed)
    is_stream_consumed = property(lambda self: self.consumed)
    content = property(lambda self: self.spec.get("body", b""))
    def __enter__(self): return self
    def __exit__(self, *args): self.close()
    def close(self): self.closed = True
    def read(self):
        self.consumed = True
        self.close()
        return self.content
    def iter_bytes(self, **kwargs):
        size = kwargs["block_size"]
        assert size == 64 * 1024
        for pos in range(0, len(self.content), size):
            data = self.content[pos:pos+size]
            self.bytes_yielded += len(data)
            yield data
            if self.spec.get("stream_error"):
                raise IOError("synthetic secret must not escape")
        self.consumed = True


class Transport(HttpClient):
    def __init__(self, specs):
        self.specs = specs
        self.calls = []
        self.responses = []
    def open(self): pass
    def close(self): pass
    def send(self, request, **kwargs):
        assert request.method in {"HEAD", "GET"}
        url = urlsplit(request.url)
        assert url.scheme == "https" and url.hostname == "synthetic-backup.oss-cn-hangzhou.aliyuncs.com"
        assert request.headers["authorization"].startswith("OSS4-HMAC-SHA256 ")
        # Never retain the Authorization or security-token headers in evidence.
        self.calls.append(dict(method=request.method, path=url.path, query=parse_qs(url.query),
            if_match=request.headers.get("if-match"), encoding=request.headers.get("accept-encoding"),
            signed_condition="if-match" in request.headers["authorization"]))
        response = Response(request, self.specs[len(self.responses)])
        self.responses.append(response)
        return response
