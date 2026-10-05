"""HTTP response bytes and thread-pool lifecycle, without binding a port."""

import io
import json
import unittest
from unittest.mock import Mock, call, patch

import server


class HttpShellExtractionTests(unittest.TestCase):
    def test_nowhere_routes_receive_original_server_context(self):
        for method in ("POST", "GET", "DELETE"):
            with self.subTest(method=method), patch.object(server.nowhere_web, "serve") as serve:
                handler = self.handler()
                handler.path = "/nowhere/fixture"
                getattr(handler, "do_" + method)()
                serve.assert_called_once_with(handler, server)

    def handler(self):
        handler = object.__new__(server.CedarToyHandler)
        handler.wfile = io.BytesIO()
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        return handler

    def test_response_bytes_headers_and_empty_status(self):
        handler = self.handler()
        payload = {"message": "测试"}
        handler._send_json(payload, status=409, extra_headers={"Set-Cookie": "fixture=1; HttpOnly"})
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.assertEqual(handler.wfile.getvalue(), raw)
        handler.send_response.assert_called_once_with(409)
        self.assertEqual(handler.send_header.call_args_list, [
            call("Content-Type", "application/json; charset=utf-8"),
            call("Content-Length", str(len(raw))),
            call("Access-Control-Allow-Origin", "*"),
            call("Access-Control-Expose-Headers", "Mcp-Session-Id"),
            call("Set-Cookie", "fixture=1; HttpOnly"),
        ])
        handler = self.handler()
        handler._send_empty(status=202, extra_headers={"Cache-Control": "no-store"})
        self.assertEqual(handler.wfile.getvalue(), b"")
        handler.send_response.assert_called_once_with(202)
        self.assertEqual(handler.send_header.call_args_list, [
            call("Access-Control-Allow-Origin", "*"), call("Content-Length", "0"),
            call("Cache-Control", "no-store"),
        ])
        handler = self.handler()
        handler._send_html_bytes(b"<p>fixture</p>", etag='"fixture"', extra_headers={"X-Test": "1"})
        self.assertEqual(handler.wfile.getvalue(), b"<p>fixture</p>")
        self.assertEqual(handler.send_header.call_args_list, [
            call("Content-Type", "text/html; charset=utf-8"), call("Content-Length", "14"),
            call("Cache-Control", "no-cache"), call("ETag", '"fixture"'), call("X-Test", "1"),
        ])

    def test_busy_response_bytes_and_socket_error(self):
        request = Mock()
        server.ThreadPoolHTTPServer._send_busy(request)
        body = b'{"error":"server busy"}'
        self.assertEqual(request.sendall.call_args.args[0],
                         b"HTTP/1.1 503 Service Unavailable\r\n"
                         b"Content-Type: application/json; charset=utf-8\r\n"
                         b"Connection: close\r\nContent-Length: 23\r\n\r\n" + body)
        request.sendall.side_effect = OSError("fixture disconnected")
        server.ThreadPoolHTTPServer._send_busy(request)

    def test_worker_queue_and_failure_release_order(self):
        httpd = object.__new__(server.ThreadPoolHTTPServer)
        httpd.worker_slots = Mock()
        httpd.executor = Mock()
        httpd._send_busy = Mock()
        httpd.close_request = Mock()
        request = object()
        with patch.object(server, "QUEUE_TIMEOUT_SECONDS", 0.25):
            httpd.worker_slots.acquire.return_value = False
            httpd.process_request(request, ("127.0.0.1", 1))
            httpd.worker_slots.acquire.assert_called_once_with(timeout=0.25)
            httpd._send_busy.assert_called_once_with(request)
            httpd.close_request.assert_called_once_with(request)
            httpd.executor.submit.assert_not_called()
            httpd.worker_slots.acquire.return_value = True
            httpd.process_request(request, ("127.0.0.1", 2))
            httpd.executor.submit.assert_called_once_with(httpd._process_request_thread, request, ("127.0.0.1", 2))
        events = Mock()
        httpd.finish_request = events.finish
        httpd.handle_error = events.error
        httpd.shutdown_request = events.shutdown
        httpd.worker_slots.release = events.release
        httpd.finish_request.side_effect = ValueError("fixture")
        httpd._process_request_thread(request, ("127.0.0.1", 2))
        self.assertEqual(events.mock_calls, [
            call.finish(request, ("127.0.0.1", 2)), call.error(request, ("127.0.0.1", 2)),
            call.shutdown(request), call.release(),
        ])

    def test_init_and_close_keep_base_lifecycle_and_runtime_factories(self):
        with (
            patch.object(server, "ThreadPoolExecutor") as executor,
            patch.object(server, "BoundedSemaphore") as semaphore,
            patch.object(server.HTTPServer, "__init__", return_value=None) as base_init,
            patch.object(server.HTTPServer, "server_close") as base_close,
        ):
            httpd = server.ThreadPoolHTTPServer(("127.0.0.1", 0), server.CedarToyHandler, max_workers=3)
            executor.assert_called_once_with(max_workers=3)
            semaphore.assert_called_once_with(3)
            base_init.assert_called_once_with(("127.0.0.1", 0), server.CedarToyHandler)
            httpd.server_close()
            base_close.assert_called_once_with()
            executor.return_value.shutdown.assert_called_once_with(wait=True)


if __name__ == "__main__":
    unittest.main()
