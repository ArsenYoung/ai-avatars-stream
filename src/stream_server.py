import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


class _Handler(BaseHTTPRequestHandler):
    server_version = "HeyGenStreamServer/1.0"

    def _send(self, code: int, body: bytes, *, content_type: str = "text/plain; charset=utf-8") -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _read_sessions(self) -> dict:
        path = Path(self.server.session_file)  # type: ignore[attr-defined]
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _serve_file(self, path: Path) -> None:
        if not path.exists() or not path.is_file():
            self._send(404, b"not found")
            return
        content = path.read_bytes()
        ext = path.suffix.lower()
        if ext == ".html":
            ctype = "text/html; charset=utf-8"
        elif ext == ".js":
            ctype = "application/javascript; charset=utf-8"
        elif ext == ".css":
            ctype = "text/css; charset=utf-8"
        else:
            ctype = "application/octet-stream"
        self._send(200, content, content_type=ctype)

    def do_GET(self) -> None:  # noqa: N802
        url = urlparse(self.path)
        if url.path == "/" or url.path == "/index.html":
            web_root = Path(self.server.web_root)  # type: ignore[attr-defined]
            self._serve_file(web_root / "index.html")
            return
        if url.path in ("/agent", "/agent.html"):
            web_root = Path(self.server.web_root)  # type: ignore[attr-defined]
            self._serve_file(web_root / "agent.html")
            return
        if url.path in ("/agent/A", "/agent/B"):
            agent = url.path.split("/")[-1]
            self.send_response(302)
            self.send_header("Location", f"/agent.html?agent={agent}")
            self.end_headers()
            return
        if url.path == "/api/session":
            qs = parse_qs(url.query)
            agent = (qs.get("agent", ["A"])[0] or "A").upper()
            payload = self._read_sessions()
            sessions = payload.get("sessions", {})
            data = sessions.get(agent)
            if not data:
                self._send(404, b'{"error":"session not found"}', content_type="application/json")
                return
            self._send(200, json.dumps(data).encode("utf-8"), content_type="application/json")
            return
        if url.path == "/api/sessions":
            payload = self._read_sessions()
            self._send(200, json.dumps(payload).encode("utf-8"), content_type="application/json")
            return
        self._send(404, b"not found")


class StreamServer:
    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 8099,
        web_root: str = "web",
        session_file: str = "stream_sessions.json",
    ):
        self.host = host
        self.port = port
        self.web_root = web_root
        self.session_file = session_file
        self._thread: threading.Thread | None = None
        self._httpd: ThreadingHTTPServer | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        web_root = Path(self.web_root)
        web_root.mkdir(parents=True, exist_ok=True)
        self._httpd = ThreadingHTTPServer((self.host, self.port), _Handler)
        self._httpd.web_root = str(web_root)  # type: ignore[attr-defined]
        self._httpd.session_file = self.session_file  # type: ignore[attr-defined]
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._httpd:
            self._httpd.shutdown()
            self._httpd.server_close()
        if self._thread:
            self._thread.join(timeout=2.0)


def main() -> None:
    host = os.getenv("STREAM_SERVER_HOST", "127.0.0.1")
    port = int(os.getenv("STREAM_SERVER_PORT", "8099"))
    web_root = os.getenv("STREAM_WEB_ROOT", "web")
    session_file = os.getenv("STREAM_SESSION_FILE", "stream_sessions.json")
    srv = StreamServer(host=host, port=port, web_root=web_root, session_file=session_file)
    srv.start()
    print(f"[stream_server] serving on http://{host}:{port}")
    try:
        while True:
            threading.Event().wait(1.0)
    except KeyboardInterrupt:
        pass
    finally:
        srv.stop()


if __name__ == "__main__":
    main()
