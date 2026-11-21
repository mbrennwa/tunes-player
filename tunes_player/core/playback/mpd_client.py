"""Minimal MPD protocol client (#60).

MPD speaks a line-based text protocol over TCP: the server greets with
``OK MPD <version>``, each command is one line, and a reply is zero or more
``key: value`` lines terminated by ``OK`` or ``ACK [code@index] {command} message``.
Only the commands Tunes needs are wrapped here.
"""

from __future__ import annotations

import re
import socket
import threading
from collections.abc import Iterable

DEFAULT_TIMEOUT_SEC = 5.0
# Connecting to a dead host should fail fast: engine creation can run on the UI thread.
CONNECT_TIMEOUT_SEC = 3.0

_ACK = re.compile(r"^ACK \[(?P<code>\d+)@(?P<index>\d+)\] \{(?P<command>[^}]*)\} (?P<message>.*)$")


class MpdError(Exception):
    """Base class for MPD client errors."""


class MpdConnectionError(MpdError, OSError):
    """The server could not be reached or the connection broke."""


class MpdCommandError(MpdError):
    """MPD answered with ``ACK``."""

    def __init__(self, code: int, command: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.command = command
        self.message = message


def quote_argument(value: object) -> str:
    """Quote one command argument for the MPD protocol."""
    text = str(value)
    if "\n" in text or "\r" in text:
        raise ValueError("MPD arguments must not contain line breaks")
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def format_command(name: str, *args: object) -> str:
    return " ".join([name, *(quote_argument(arg) for arg in args)])


class MpdClient:
    """One blocking MPD connection; calls are serialized with a lock."""

    def __init__(
        self,
        host: str,
        port: int,
        *,
        timeout: float = DEFAULT_TIMEOUT_SEC,
    ) -> None:
        self.host = host
        self.port = port
        self._timeout = timeout
        self._lock = threading.RLock()
        self._sock: socket.socket | None = None
        self._reader = None
        self.protocol_version: str | None = None

    # -- connection ----------------------------------------------------

    @property
    def connected(self) -> bool:
        return self._sock is not None

    def connect(self) -> None:
        with self._lock:
            self.close()
            try:
                sock = socket.create_connection(
                    (self.host, self.port),
                    timeout=min(self._timeout, CONNECT_TIMEOUT_SEC),
                )
            except OSError as exc:
                raise MpdConnectionError(
                    f"Could not connect to MPD at {self.host}:{self.port}: {exc}"
                ) from exc
            sock.settimeout(self._timeout)
            self._sock = sock
            self._reader = sock.makefile("rb")
            try:
                greeting = self._readline()
            except MpdConnectionError:
                self.close()
                raise
            if not greeting.startswith("OK MPD"):
                self.close()
                raise MpdConnectionError(
                    f"{self.host}:{self.port} is not an MPD server (greeting: {greeting!r})"
                )
            self.protocol_version = greeting.split(" ", 2)[2] if greeting.count(" ") >= 2 else None

    def close(self) -> None:
        with self._lock:
            reader, sock = self._reader, self._sock
            self._reader = None
            self._sock = None
            for item in (reader, sock):
                if item is None:
                    continue
                try:
                    item.close()
                except OSError:
                    pass

    # -- protocol ------------------------------------------------------

    def _readline(self) -> str:
        reader = self._reader
        if reader is None:
            raise MpdConnectionError("Not connected to MPD")
        try:
            raw = reader.readline()
        except (OSError, ValueError) as exc:
            self.close()
            raise MpdConnectionError(f"MPD connection error: {exc}") from exc
        if not raw:
            self.close()
            raise MpdConnectionError("MPD closed the connection")
        return raw.decode("utf-8", errors="replace").rstrip("\n")

    def _send_and_read(self, line: str) -> list[tuple[str, str]]:
        sock = self._sock
        if sock is None:
            raise MpdConnectionError("Not connected to MPD")
        try:
            sock.sendall(line.encode("utf-8") + b"\n")
        except OSError as exc:
            self.close()
            raise MpdConnectionError(f"MPD connection error: {exc}") from exc
        pairs: list[tuple[str, str]] = []
        while True:
            text = self._readline()
            if text == "OK":
                return pairs
            if text.startswith("ACK"):
                match = _ACK.match(text)
                if match is None:
                    raise MpdCommandError(0, line.split(" ", 1)[0], text)
                raise MpdCommandError(
                    int(match["code"]), match["command"], match["message"]
                )
            key, sep, value = text.partition(": ")
            if not sep:
                key, _, value = text.partition(":")
            pairs.append((key.strip().casefold(), value))

    def command(self, name: str, *args: object) -> list[tuple[str, str]]:
        """Run one command; reconnect once if the connection was dropped."""
        line = format_command(name, *args)
        with self._lock:
            if self._sock is None:
                self.connect()
            try:
                return self._send_and_read(line)
            except MpdConnectionError:
                self.connect()
                return self._send_and_read(line)

    def command_dict(self, name: str, *args: object) -> dict[str, str]:
        result: dict[str, str] = {}
        for key, value in self.command(name, *args):
            result[key] = value
        return result

    # -- commands Tunes uses -------------------------------------------

    def ping(self) -> None:
        self.command("ping")

    def status(self) -> dict[str, str]:
        return self.command_dict("status")

    def currentsong(self) -> dict[str, str]:
        return self.command_dict("currentsong")

    def clear(self) -> None:
        self.command("clear")

    def add(self, uri: str) -> None:
        self.command("add", uri)

    def play(self, position: int = 0) -> None:
        self.command("play", position)

    def pause(self, paused: bool) -> None:
        self.command("pause", 1 if paused else 0)

    def stop(self) -> None:
        self.command("stop")

    def seekcur(self, position_sec: float) -> None:
        self.command("seekcur", f"{max(0.0, position_sec):.3f}")

    def command_list(self, commands: Iterable[tuple[str, tuple[object, ...]]]) -> None:
        """Run several commands atomically (``command_list_begin`` block)."""
        lines = ["command_list_begin"]
        lines.extend(format_command(name, *args) for name, args in commands)
        lines.append("command_list_end")
        block = "\n".join(lines)
        with self._lock:
            if self._sock is None:
                self.connect()
            try:
                self._send_and_read(block)
            except MpdConnectionError:
                self.connect()
                self._send_and_read(block)


def parse_status_time(status: dict[str, str]) -> tuple[float, float | None]:
    """Return ``(elapsed_sec, duration_sec)`` from an MPD ``status`` reply."""
    elapsed = _float_or_none(status.get("elapsed"))
    duration = _float_or_none(status.get("duration"))
    if elapsed is None or duration is None:
        time_field = status.get("time", "")
        left, sep, right = time_field.partition(":")
        if sep:
            if elapsed is None:
                elapsed = _float_or_none(left)
            if duration is None:
                duration = _float_or_none(right)
    if duration is not None and duration <= 0:
        duration = None
    return (elapsed if elapsed is not None else 0.0), duration


def _float_or_none(value: str | None) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except ValueError:
        return None


def probe_mpd(host: str, port: int, *, timeout: float = CONNECT_TIMEOUT_SEC) -> str:
    """Connect, ping and disconnect; return the MPD protocol version.

    Raises :class:`MpdError` when the server is unreachable or not MPD.
    """
    client = MpdClient(host, port, timeout=timeout)
    try:
        client.connect()
        client.ping()
        return client.protocol_version or "unknown"
    finally:
        client.close()
