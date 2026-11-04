"""Flask static responses through the real proxy method, without network/saves."""

import base64
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import server


ROOT = Path(__file__).resolve().parents[1]
ENGINE = Path(os.environ.get("GARDEN_CAT_ENGINE_ROOT", ROOT / "vendor" / "Garden-Cat-Engine"))
PYTHON = os.environ.get("GARDEN_TEST_PYTHON", str(ENGINE / ".venv/bin/python"))


class GardenStaticProxyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Import Flask only inside its existing venv, with all stores isolated
        # before import. No server is started and no game state route is called.
        with tempfile.TemporaryDirectory(prefix="garden-static-proxy-") as tmp:
            code = r'''
import base64, json, os, sys
os.environ['GARDEN_CAT_SAVE_ROOT'] = sys.argv[1] + '/saves'
os.environ['GARDEN_CAT_NOTES_DB'] = sys.argv[1] + '/notes.db'
import game_api
client = game_api.app.test_client()
results = {}
def record(key, url, headers=None):
    response = client.get(url, headers=headers or {})
    results[key] = {'status': response.status_code, 'headers': list(response.headers),
                    'body': base64.b64encode(response.data).decode()}
    return response
for name in ('app.js', 'style.css'):
    url = '/static/' + name + '?v=' + game_api.ASSET_VERSION
    response = record(name, url)
    record(name + '.etag', url, {'If-None-Match': response.headers['ETag']})
    record(name + '.date', url, {'If-Modified-Since': response.headers['Last-Modified']})
    record(name + '.changed', url, {'If-None-Match': '"old-representation"',
                                   'If-Modified-Since': response.headers['Last-Modified']})
record('range', '/static/app.js', {'Range': 'bytes=0-255'})
record('page', '/web/', {'X-Forwarded-Prefix': '/garden-cat'})
print(json.dumps(results))
'''
            result = subprocess.run(
                [PYTHON, "-B", "-c", code, tmp],
                cwd=ENGINE, check=True, capture_output=True, timeout=20,
            )
            cls.responses = json.loads(result.stdout)
        for response in cls.responses.values():
            response["body"] = base64.b64decode(response["body"])

    def proxy(self, response, incoming=None):
        captured = {}

        class Upstream:
            status = response["status"]
            reason = "fixture"
            def read(self):
                return response["body"]
            def getheaders(self):
                return response["headers"]

        class Connection:
            def __init__(self, *_args, **_kwargs):
                pass
            def request(self, method, target, body=None, headers=None):
                captured.update(target=target, forwarded=headers)
            def getresponse(self):
                return Upstream()
            def close(self):
                pass

        handler = object.__new__(server.CedarToyHandler)
        handler.headers = incoming or {}
        handler.client_address = ("127.0.0.1", 1)
        handler.command = "GET"
        handler.rfile = BytesIO()
        handler.wfile = BytesIO()
        headers = []
        handler.send_response = lambda status, *_: captured.update(status=status)
        handler.send_header = lambda k, v: headers.append((k, v))
        handler.end_headers = lambda: None
        with patch.object(server.http.client, "HTTPConnection", Connection):
            handler._proxy_to_garden_cat("GET", "/static/app.js", "v=fixture")
        return captured, dict(headers), handler.wfile.getvalue()

    def test_revalidated_assets_do_not_claim_cached_representation_has_zero_length(self):
        for name in ("app.js", "style.css"):
            original = dict(self.responses[name]["headers"])
            for suffix, condition in (("etag", "If-None-Match"), ("date", "If-Modified-Since")):
                with self.subTest(asset=name, condition=condition):
                    value = original["ETag" if suffix == "etag" else "Last-Modified"]
                    response = self.responses[name + "." + suffix]
                    captured, headers, body = self.proxy(response, {condition: value})
                    self.assertEqual(captured["status"], 304)
                    self.assertEqual(captured["forwarded"][condition], value)
                    self.assertEqual(body, b"")
                    # RFC 9110 §8.6: 304 length, if present, means the 200 size,
                    # not the empty 304 body. Safest here is to omit the field.
                    self.assertNotIn("Content-Length", headers)
                    self.assertEqual(headers["ETag"], original["ETag"])
                    self.assertEqual(headers["Cache-Control"], "no-cache")

    def test_full_and_changed_validator_responses_keep_exact_bytes_and_metadata(self):
        for name in ("app.js", "style.css"):
            source = (ENGINE / "static" / name).read_bytes()
            for suffix in ("", ".changed"):
                with self.subTest(asset=name, variant=suffix):
                    captured, headers, body = self.proxy(self.responses[name + suffix])
                    self.assertEqual(captured["status"], 200)
                    self.assertEqual(int(headers["Content-Length"]), len(source))
                    self.assertEqual(hashlib.sha256(body).digest(), hashlib.sha256(source).digest())
                    self.assertIn("charset=utf-8", headers["Content-Type"])
                    self.assertNotIn("Content-Encoding", headers)

    def test_partial_response_is_not_rewritten_as_full_or_empty(self):
        captured, headers, body = self.proxy(self.responses["range"], {"Range": "bytes=0-255"})
        self.assertEqual(captured["status"], 206)
        self.assertEqual(headers["Content-Length"], "256")
        self.assertTrue(headers["Content-Range"].startswith("bytes 0-255/"))
        self.assertEqual(body, (ENGINE / "static/app.js").read_bytes()[:256])

    def test_page_prefix_and_revalidation_policy_are_preserved(self):
        response = self.responses["page"]
        captured, headers, body = self.proxy(response)
        self.assertEqual(captured["status"], 200)
        self.assertEqual(int(headers["Content-Length"]), len(body))
        self.assertIn("no-store", headers["Cache-Control"])
        self.assertIn(b'<base href="/garden-cat/"', body)
        self.assertIn(b'src="/garden-cat/static/app.js?v=', body)
        self.assertIn(b'href="/garden-cat/static/style.css?v=', body)
        self.assertIn(b"window.CEDAR_GARDEN_BOOT", body)


if __name__ == "__main__":
    unittest.main()
