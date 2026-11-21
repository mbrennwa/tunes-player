"""In-process fake MPD server for tests (#60).

Implements just enough of the MPD line protocol for ``MpdClient`` / ``MpdEngine``:
``ping``, ``status``, ``currentsong``, ``clear``, ``add``, ``play``, ``pause``,
``stop``, ``seek``, ``seekcur``, ``clearerror`` and ``command_list_begin`` blocks.

Tests drive the "player" directly: set :attr:`elapsed`, call :meth:`finish_track`
for a natural end of track, or set :attr:`busy` to make opening the output fail
the way a single-owner ALSA device does (MPD stays in ``pause`` with ``error``).
"""

from __future__ import annotations

import socketserver
import threading

BUSY_MESSAGE = 'Failed to open "out" (alsa); Failed to open ALSA device "hw:0,0": Device or resource busy'


class _Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        server: FakeMpdServer = self.server.fake  # type: ignore[attr-defined]
        self.wfile.write(b"OK MPD 0.23.12\n")
        in_list = False
        batch: list[str] = []
        while True:
            raw = self.rfile.readline()
            if not raw:
                return
            line = raw.decode("utf-8").rstrip("\n")
            if line == "command_list_begin":
                in_list, batch = True, []
                continue
            if line == "command_list_end":
                in_list = False
                reply = server.run_batch(batch)
                self.wfile.write(reply.encode("utf-8"))
                continue
            if in_list:
                batch.append(line)
                continue
            if server.drop_next_connection:
                server.drop_next_connection = False
                return
            self.wfile.write(server.run_batch([line]).encode("utf-8"))


class _Server(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


class FakeMpdServer:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.queue: list[str] = []
        self.state = "stop"
        self.elapsed = 0.0
        self.duration = 200.0
        self.error: str | None = None
        self.busy = False
        self.commands: list[str] = []
        self.drop_next_connection = False
        self._server = _Server(("127.0.0.1", 0), _Handler)
        self._server.fake = self  # type: ignore[attr-defined]
        self.host, self.port = self._server.server_address
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    def __enter__(self) -> FakeMpdServer:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # -- test controls -------------------------------------------------

    def finish_track(self) -> None:
        """Natural end of the (only) queued track: MPD goes to ``stop``."""
        with self._lock:
            self.state = "stop"
            self.elapsed = 0.0

    def commands_named(self, name: str) -> list[str]:
        with self._lock:
            return [item for item in self.commands if item.split(" ", 1)[0] == name]

    # -- protocol ------------------------------------------------------

    def run_batch(self, lines: list[str]) -> str:
        out: list[str] = []
        with self._lock:
            for line in lines:
                self.commands.append(line)
                name, _, rest = line.partition(" ")
                args = [part.strip('"') for part in rest.split(" ") if part]
                reply = self._run(name, args)
                if reply.startswith("ACK"):
                    return reply
                out.append(reply)
        return "".join(out) + "OK\n"

    def _open_output(self) -> None:
        if self.busy:
            self.state = "pause"
            self.error = BUSY_MESSAGE
        else:
            self.state = "play"
            self.error = None

    def _run(self, name: str, args: list[str]) -> str:
        if name == "ping":
            return ""
        if name == "status":
            lines = ["volume: -1", "repeat: 0", f"state: {self.state}"]
            if self.queue:
                lines.append("song: 0")
                lines.append(f"elapsed: {self.elapsed:.3f}")
                lines.append(f"duration: {self.duration:.3f}")
            if self.error:
                lines.append(f"error: {self.error}")
            return "\n".join(lines) + "\n"
        if name == "currentsong":
            return f"file: {self.queue[0]}\n" if self.queue else ""
        if name == "clear":
            self.queue = []
            self.state = "stop"
            self.elapsed = 0.0
            return ""
        if name == "add":
            if not args:
                return "ACK [2@0] {add} wrong number of arguments\n"
            if args[0].endswith("missing.flac"):
                return "ACK [50@0] {add} No such file or directory\n"
            self.queue.append(args[0])
            return ""
        if name == "play":
            if not self.queue:
                return "ACK [50@0] {play} Bad song index\n"
            self.elapsed = 0.0
            self._open_output()
            return ""
        if name == "seek":
            if not self.queue:
                return "ACK [50@0] {seek} Bad song index\n"
            self.elapsed = float(args[1])
            self._open_output()
            return ""
        if name == "seekcur":
            self.elapsed = float(args[0])
            return ""
        if name == "pause":
            want_pause = args[0] == "1" if args else self.state == "play"
            if want_pause:
                if self.state == "play":
                    self.state = "pause"
            elif self.state == "pause":
                self._open_output()
            return ""
        if name == "stop":
            self.state = "stop"
            self.elapsed = 0.0
            return ""
        if name == "clearerror":
            self.error = None
            return ""
        return f"ACK [5@0] {{{name}}} unknown command\n"
