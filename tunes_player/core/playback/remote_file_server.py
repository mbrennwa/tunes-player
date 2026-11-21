"""Short-lived HTTP file server for remote MPD outputs (#60).

A remote MPD cannot open ``file://`` paths on the Tunes host, so local library
files are handed to it as ``http://`` URLs served from here. Only files that were
explicitly registered are reachable, each behind an unguessable token; there is
no directory listing and the URL's file name is only an extension hint for MPD.
HTTP ``Range`` requests are supported because MPD seeks by range.
"""

from __future__ import annotations

import logging
import mimetypes
import re
import secrets
import socket
import threading
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote

log = logging.getLogger(__name__)

# Current track plus a few prefetched ones; older tokens are dropped.
DEFAULT_MAX_TOKENS = 16
_CHUNK = 64 * 1024
_RANGE = re.compile(r"^bytes=(\d*)-(\d*)$")


def local_address_for(remote_host: str, remote_port: int) -> str:
    """Local interface address the OS would use to reach ``remote_host``."""
    try:
        infos = socket.getaddrinfo(
            remote_host, remote_port, socket.AF_INET, socket.SOCK_DGRAM
        )
    except socket.gaierror as exc:
        raise OSError(f"Could not resolve {remote_host}: {exc}") from exc
    family, socktype, proto, _canon, sockaddr = infos[0]
    with socket.socket(family, socktype, proto) as probe:
        # UDP connect sends nothing; it only selects the outgoing interface.
        probe.connect(sockaddr)
        return str(probe.getsockname()[0])


def parse_range_header(value: str | None, size: int) -> tuple[int, int] | None | str:
    """Return ``(start, end)`` inclusive, None for no/ignored header, ``"invalid"`` for 416."""
    if not value:
        return None
    match = _RANGE.match(value.strip())
    if match is None:
        return None
    first, last = match.group(1), match.group(2)
    if first == "" and last == "":
        return None
    if first == "":
        suffix = int(last)
        if suffix == 0:
            return "invalid"
        return max(0, size - suffix), size - 1
    start = int(first)
    end = int(last) if last else size - 1
    if start >= size or end < start:
        return "invalid"
    return start, min(end, size - 1)


class RemoteFileServer:
    """Serves registered local files to a remote MPD over HTTP."""

    def __init__(self, *, max_tokens: int = DEFAULT_MAX_TOKENS) -> None:
        self._max_tokens = max(1, max_tokens)
        self._lock = threading.Lock()
        self._tokens: OrderedDict[str, Path] = OrderedDict()
        self._by_path: dict[Path, str] = {}
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._bind_address: str | None = None

    # -- lifecycle -----------------------------------------------------

    @property
    def running(self) -> bool:
        return self._httpd is not None

    @property
    def address(self) -> tuple[str, int] | None:
        httpd = self._httpd
        if httpd is None:
            return None
        host, port = httpd.server_address[:2]
        return str(host), int(port)

    def serve_to(self, remote_host: str, remote_port: int) -> None:
        """Make sure the server is bound to the interface that reaches the MPD host."""
        address = local_address_for(remote_host, remote_port)
        with self._lock:
            if self._httpd is not None and self._bind_address == address:
                return
        self.stop()
        self._start(address)

    def _start(self, address: str) -> None:
        server = self

        class Handler(_FileHandler):
            file_server = server

        httpd = ThreadingHTTPServer((address, 0), Handler)
        httpd.daemon_threads = True
        thread = threading.Thread(
            target=httpd.serve_forever, name="tunes-remote-files", daemon=True
        )
        with self._lock:
            self._httpd = httpd
            self._thread = thread
            self._bind_address = address
        thread.start()
        log.info("Remote file server listening on %s:%s", *httpd.server_address[:2])

    def stop(self) -> None:
        with self._lock:
            httpd, thread = self._httpd, self._thread
            self._httpd = None
            self._thread = None
            self._bind_address = None
            self._tokens.clear()
            self._by_path.clear()
        if httpd is not None:
            httpd.shutdown()
            httpd.server_close()
        if thread is not None:
            thread.join(timeout=2.0)

    # -- registration --------------------------------------------------

    def url_for(self, path: Path) -> str:
        """Register ``path`` and return the URL a remote MPD can fetch it from."""
        address = self.address
        if address is None:
            raise RuntimeError("Remote file server is not running")
        resolved = path.resolve()
        with self._lock:
            token = self._by_path.get(resolved)
            if token is None or token not in self._tokens:
                token = secrets.token_urlsafe(16)
                self._tokens[token] = resolved
                self._by_path[resolved] = token
            self._tokens.move_to_end(token)
            while len(self._tokens) > self._max_tokens:
                old_token, old_path = self._tokens.popitem(last=False)
                if self._by_path.get(old_path) == old_token:
                    del self._by_path[old_path]
        host, port = address
        return f"http://{host}:{port}/f/{token}/{quote(resolved.name)}"

    def release_all(self) -> None:
        """Forget every registered file (output changed or playback stopped)."""
        with self._lock:
            self._tokens.clear()
            self._by_path.clear()

    def _lookup(self, token: str) -> Path | None:
        with self._lock:
            return self._tokens.get(token)


class _FileHandler(BaseHTTPRequestHandler):
    file_server: RemoteFileServer
    server_version = "TunesFileServer"
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        log.debug("remote files: " + format, *args)

    def do_HEAD(self) -> None:  # noqa: N802
        self._serve(send_body=False)

    def do_GET(self) -> None:  # noqa: N802
        self._serve(send_body=True)

    def _resolve(self) -> Path | None:
        parts = self.path.split("?", 1)[0].split("/")
        # "/f/<token>/<name>" -> ["", "f", token, name]
        if len(parts) != 4 or parts[0] != "" or parts[1] != "f":
            return None
        path = self.file_server._lookup(parts[2])
        if path is None or not path.is_file():
            return None
        return path

    def _serve(self, *, send_body: bool) -> None:
        path = self._resolve()
        if path is None:
            self._reply_error(404)
            return
        try:
            size = path.stat().st_size
        except OSError:
            self._reply_error(404)
            return
        requested = parse_range_header(self.headers.get("Range"), size)
        if requested == "invalid":
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{size}")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if isinstance(requested, tuple):
            start, end = requested
            status = 206
        else:
            start, end = 0, size - 1
            status = 200
        length = max(0, end - start + 1) if size else 0
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if not send_body or length == 0:
            return
        try:
            with path.open("rb") as handle:
                handle.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = handle.read(min(_CHUNK, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            # MPD closes the connection when it seeks or stops; not an error.
            self.close_connection = True

    def _reply_error(self, code: int) -> None:
        self.send_response(code)
        self.send_header("Content-Length", "0")
        self.end_headers()
