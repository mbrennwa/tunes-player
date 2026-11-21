"""MPD protocol client (#60)."""

from __future__ import annotations

import unittest

from tests.fake_mpd import FakeMpdServer
from tunes_player.core.playback.mpd_client import (
    MpdClient,
    MpdCommandError,
    MpdConnectionError,
    format_command,
    parse_status_time,
    probe_mpd,
    quote_argument,
)


class QuotingTests(unittest.TestCase):
    def test_quotes_and_escapes(self) -> None:
        self.assertEqual(quote_argument("a b"), '"a b"')
        self.assertEqual(quote_argument('say "hi"'), '"say \\"hi\\""')
        self.assertEqual(quote_argument("back\\slash"), '"back\\\\slash"')

    def test_rejects_line_breaks(self) -> None:
        with self.assertRaises(ValueError):
            quote_argument("a\nb")

    def test_format_command(self) -> None:
        self.assertEqual(format_command("add", "http://h/x.flac"), 'add "http://h/x.flac"')
        self.assertEqual(format_command("clear"), "clear")


class StatusParsingTests(unittest.TestCase):
    def test_elapsed_and_duration(self) -> None:
        self.assertEqual(
            parse_status_time({"elapsed": "12.500", "duration": "200.000"}),
            (12.5, 200.0),
        )

    def test_falls_back_to_time_field(self) -> None:
        self.assertEqual(parse_status_time({"time": "30:240"}), (30.0, 240.0))

    def test_missing_values(self) -> None:
        self.assertEqual(parse_status_time({}), (0.0, None))
        self.assertEqual(parse_status_time({"elapsed": "1", "duration": "0"}), (1.0, None))


class ClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = FakeMpdServer()
        self.addCleanup(self.server.close)
        self.client = MpdClient(self.server.host, self.server.port, timeout=2.0)
        self.addCleanup(self.client.close)

    def test_connect_reads_greeting(self) -> None:
        self.client.connect()
        self.assertTrue(self.client.connected)
        self.assertEqual(self.client.protocol_version, "0.23.12")

    def test_status_returns_key_values(self) -> None:
        status = self.client.status()
        self.assertEqual(status["state"], "stop")

    def test_ack_raises_command_error(self) -> None:
        self.client.connect()
        with self.assertRaises(MpdCommandError) as ctx:
            self.client.add("/nope/missing.flac")
        self.assertEqual(ctx.exception.code, 50)
        self.assertIn("No such file", ctx.exception.message)
        # The connection stays usable after an ACK.
        self.assertEqual(self.client.status()["state"], "stop")

    def test_command_list_runs_in_order(self) -> None:
        self.client.command_list([("clear", ()), ("add", ("http://h/a.flac",)), ("play", (0,))])
        self.assertEqual(self.server.queue, ["http://h/a.flac"])
        self.assertEqual(self.server.state, "play")

    def test_reconnects_after_dropped_connection(self) -> None:
        self.client.connect()
        self.client.ping()
        self.server.drop_next_connection = True
        self.assertEqual(self.client.status()["state"], "stop")

    def test_unreachable_host_raises_connection_error(self) -> None:
        port = self.server.port
        self.server.close()
        client = MpdClient("127.0.0.1", port, timeout=1.0)
        with self.assertRaises(MpdConnectionError):
            client.connect()

    def test_probe_returns_version(self) -> None:
        self.assertEqual(probe_mpd(self.server.host, self.server.port), "0.23.12")

    def test_probe_fails_for_non_mpd_server(self) -> None:
        import socketserver
        import threading

        class Handler(socketserver.BaseRequestHandler):
            def handle(self) -> None:
                self.request.sendall(b"HTTP/1.1 400 Bad Request\r\n")

        with socketserver.TCPServer(("127.0.0.1", 0), Handler) as other:
            thread = threading.Thread(target=other.serve_forever, daemon=True)
            thread.start()
            try:
                with self.assertRaises(MpdConnectionError):
                    probe_mpd("127.0.0.1", other.server_address[1], timeout=1.0)
            finally:
                other.shutdown()


if __name__ == "__main__":
    unittest.main()
