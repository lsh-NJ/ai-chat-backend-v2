"""Small OpenAI-compatible HTTP server used only by the load-test stack."""

import json
import os
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

HOST = "0.0.0.0"
PORT = 8081
DELAY_SECONDS = int(os.environ.get("MOCK_LLM_DELAY_MS", "30")) / 1000
REPLY_PARTS = ("mock ", "assistant ", "reply")


class MockLLMHandler(BaseHTTPRequestHandler):
    server_version = "MockLLM/1.0"

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _send_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send_json(HTTPStatus.OK, {"status": "ok"})
            return
        self._send_json(HTTPStatus.NOT_FOUND, {"detail": "not found"})

    def do_POST(self) -> None:
        if self.path != "/chat/completions":
            self._send_json(HTTPStatus.NOT_FOUND, {"detail": "not found"})
            return

        try:
            content_length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(content_length))
        except (TypeError, ValueError, json.JSONDecodeError):
            self._send_json(HTTPStatus.BAD_REQUEST, {"detail": "invalid JSON"})
            return

        if payload.get("stream") is True:
            self._send_stream()
            return

        time.sleep(DELAY_SECONDS)
        self._send_json(
            HTTPStatus.OK,
            {
                "choices": [
                    {"message": {"role": "assistant", "content": "".join(REPLY_PARTS)}}
                ]
            },
        )

    def _send_stream(self) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        for part in REPLY_PARTS:
            time.sleep(DELAY_SECONDS / len(REPLY_PARTS))
            event = {"choices": [{"delta": {"content": part}}]}
            self.wfile.write(f"data: {json.dumps(event)}\n\n".encode())
            self.wfile.flush()

        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), MockLLMHandler)
    server.daemon_threads = True
    server.serve_forever()


if __name__ == "__main__":
    main()
