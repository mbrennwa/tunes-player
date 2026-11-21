"""HTTP file server that hands local files to a remote MPD (#60)."""

from __future__ import annotations

import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from tunes_player.core.playback.remote_file_server import (
    RemoteFileServer,
    parse_range_header,
)


class RangeHeaderTests(unittest.TestCase):
    def test_no_header(self) -> None:
        self.assertIsNone(parse_range_header(None, 100))
        self.assertIsNone(parse_range_header("", 100))

    def test_closed_open_and_suffix_ranges(self) -> None:
        self.assertEqual(parse_range_header("bytes=10-19", 100), (10, 19))
        self.assertEqual(parse_range_header("bytes=90-", 100), (90, 99))
        self.assertEqual(parse_range_header("bytes=-10", 100), (90, 99))
        self.assertEqual(parse_range_header("bytes=-500", 100), (0, 99))

    def test_end_is_clamped(self) -> None:
        self.assertEqual(parse_range_header("bytes=10-999", 100), (10, 99))

    def test_unsatisfiable(self) -> None:
        self.assertEqual(parse_range_header("bytes=100-", 100), "invalid")
        self.assertEqual(parse_range_header("bytes=20-10", 100), "invalid")
        self.assertEqual(parse_range_header("bytes=-0", 100), "invalid")

    def test_unsupported_syntax_is_ignored(self) -> None:
        self.assertIsNone(parse_range_header("bytes=0-1,5-6", 100))
        self.assertIsNone(parse_range_header("items=0-1", 100))


class RemoteFileServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.data = bytes(range(256)) * 40
        self.file = Path(self._tmp.name) / "my track.flac"
        self.file.write_bytes(self.data)
        self.server = RemoteFileServer()
        self.addCleanup(self.server.stop)
        self.server.serve_to("127.0.0.1", 6600)

    def _get(self, url: str, headers: dict[str, str] | None = None, method: str = "GET"):
        request = urllib.request.Request(url, headers=headers or {}, method=method)
        return urllib.request.urlopen(request, timeout=3)

    def _status(self, url: str) -> int:
        try:
            with self._get(url) as response:
                return response.status
        except urllib.error.HTTPError as exc:
            exc.close()
            return exc.code

    def test_registered_file_is_served(self) -> None:
        url = self.server.url_for(self.file)
        self.assertTrue(url.startswith("http://127.0.0.1:"))
        self.assertTrue(url.endswith("/my%20track.flac"))
        with self._get(url) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(response.headers["Accept-Ranges"], "bytes")
            self.assertEqual(response.read(), self.data)

    def test_head_reports_length_without_body(self) -> None:
        url = self.server.url_for(self.file)
        with self._get(url, method="HEAD") as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(int(response.headers["Content-Length"]), len(self.data))
            self.assertEqual(response.read(), b"")

    def test_range_request_returns_partial_content(self) -> None:
        url = self.server.url_for(self.file)
        with self._get(url, {"Range": "bytes=100-199"}) as response:
            self.assertEqual(response.status, 206)
            self.assertEqual(
                response.headers["Content-Range"], f"bytes 100-199/{len(self.data)}"
            )
            self.assertEqual(response.read(), self.data[100:200])

    def test_suffix_range(self) -> None:
        url = self.server.url_for(self.file)
        with self._get(url, {"Range": "bytes=-50"}) as response:
            self.assertEqual(response.status, 206)
            self.assertEqual(response.read(), self.data[-50:])

    def test_unsatisfiable_range_is_416(self) -> None:
        url = self.server.url_for(self.file)
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._get(url, {"Range": f"bytes={len(self.data)}-"})
        ctx.exception.close()
        self.assertEqual(ctx.exception.code, 416)

    def test_unknown_token_is_404(self) -> None:
        url = self.server.url_for(self.file)
        bad = url.replace("/f/", "/f/x", 1)
        self.assertEqual(self._status(bad), 404)

    def test_other_paths_are_404(self) -> None:
        host, port = self.server.address or ("", 0)
        base = f"http://{host}:{port}"
        for path in ("/", "/f/", "/etc/passwd", "/f/../../etc/passwd", "/f/a/b/c"):
            self.assertEqual(self._status(base + path), 404, path)

    def test_same_file_gets_same_token(self) -> None:
        self.assertEqual(self.server.url_for(self.file), self.server.url_for(self.file))

    def test_oldest_token_is_evicted(self) -> None:
        server = RemoteFileServer(max_tokens=2)
        self.addCleanup(server.stop)
        server.serve_to("127.0.0.1", 6600)
        files = []
        for index in range(3):
            path = Path(self._tmp.name) / f"t{index}.flac"
            path.write_bytes(b"x" * 10)
            files.append(path)
        urls = [server.url_for(path) for path in files]
        self.assertEqual(self._status(urls[0]), 404)
        self.assertEqual(self._status(urls[1]), 200)
        self.assertEqual(self._status(urls[2]), 200)

    def test_release_all_forgets_files(self) -> None:
        url = self.server.url_for(self.file)
        self.server.release_all()
        self.assertEqual(self._status(url), 404)
        # Still running: a new registration works.
        self.assertEqual(self._status(self.server.url_for(self.file)), 200)

    def test_deleted_file_is_404(self) -> None:
        url = self.server.url_for(self.file)
        self.file.unlink()
        self.assertEqual(self._status(url), 404)

    def test_stop_shuts_the_server_down(self) -> None:
        url = self.server.url_for(self.file)
        self.server.stop()
        self.assertFalse(self.server.running)
        with self.assertRaises(OSError):
            self._get(url)
        with self.assertRaises(RuntimeError):
            self.server.url_for(self.file)


if __name__ == "__main__":
    unittest.main()
