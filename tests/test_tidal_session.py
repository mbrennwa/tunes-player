"""Tests for TIDAL session persistence (no network)."""

from __future__ import annotations

import json
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests

from tunes_player.core.backends.tidal.client import (
    TidalClient,
    TidalUnavailableError,
    _SessionErrorKind,
    _classify_session_error,
    _session_file_has_credentials,
)


def _write_session_file(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "token_type": {"data": "Bearer"},
                "session_id": {"data": "sess-123"},
                "access_token": {"data": "access-abc"},
                "refresh_token": {"data": "refresh-xyz"},
                "is_pkce": {"data": True},
            }
        ),
        encoding="utf-8",
    )


class TestSessionFileCredentials(unittest.TestCase):
    def test_session_file_has_credentials(self) -> None:
        path = Path(self._testMethodName) / "tidal-session.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_session_file(path)
        self.assertTrue(_session_file_has_credentials(path))

    def test_missing_file_has_no_credentials(self) -> None:
        self.assertFalse(_session_file_has_credentials(Path("/nonexistent/tidal-session.json")))


class TestClassifySessionError(unittest.TestCase):
    def test_connection_error_is_transient(self) -> None:
        self.assertEqual(
            _classify_session_error(requests.ConnectionError("offline")).name,
            "TRANSIENT",
        )

    def test_timeout_is_transient(self) -> None:
        self.assertEqual(
            _classify_session_error(requests.Timeout("slow")).name,
            "TRANSIENT",
        )


def _mock_tidalapi_session(*, session_instance: MagicMock | None = None) -> MagicMock:
    module = MagicMock()
    instance = session_instance or MagicMock()
    instance.refresh_token = "refresh-xyz"
    instance.access_token = "access-abc"
    instance.session_id = "sess-123"
    instance.is_pkce = True
    instance.check_login.return_value = True
    instance.token_refresh.return_value = True
    instance.load_session_from_file.return_value = True
    module.Session.return_value = instance
    module.exceptions.AuthenticationError = type("AuthenticationError", (Exception,), {})
    return module, instance


class TestTidalClientSessionPersistence(unittest.TestCase):
    def test_is_logged_in_true_from_file_without_network(self) -> None:
        session_file = Path(self._testMethodName) / "tidal-session.json"
        session_file.parent.mkdir(parents=True, exist_ok=True)
        _write_session_file(session_file)
        client = TidalClient(session_file)
        with patch(
            "tunes_player.core.backends.tidal.client.tidalapi_available",
            return_value=True,
        ):
            self.assertTrue(client.is_logged_in())

    def test_transient_error_does_not_delete_session_file(self) -> None:
        session_file = Path(self._testMethodName) / "tidal-session.json"
        session_file.parent.mkdir(parents=True, exist_ok=True)
        _write_session_file(session_file)
        tidalapi, instance = _mock_tidalapi_session()
        instance.check_login.side_effect = requests.ConnectionError("offline")
        client = TidalClient(session_file)
        with (
            patch(
                "tunes_player.core.backends.tidal.client.tidalapi_available",
                return_value=True,
            ),
            patch.dict(sys.modules, {"tidalapi": tidalapi}),
        ):
            self.assertTrue(client.is_logged_in())
            with self.assertRaises(TidalUnavailableError) as ctx:
                client._require_login()
            self.assertIn("temporarily unavailable", str(ctx.exception).lower())
            self.assertTrue(session_file.is_file())

    def test_auth_failure_clears_session_file(self) -> None:
        session_file = Path(self._testMethodName) / "tidal-session.json"
        session_file.parent.mkdir(parents=True, exist_ok=True)
        _write_session_file(session_file)
        tidalapi, instance = _mock_tidalapi_session()
        instance.check_login.side_effect = tidalapi.exceptions.AuthenticationError(
            "invalid_grant",
        )
        empty_session = MagicMock()
        empty_session.refresh_token = None
        empty_session.access_token = None
        empty_session.session_id = None
        tidalapi.Session.side_effect = [instance, empty_session]
        client = TidalClient(session_file)
        with (
            patch(
                "tunes_player.core.backends.tidal.client.tidalapi_available",
                return_value=True,
            ),
            patch(
                "tunes_player.core.backends.tidal.client._classify_session_error",
                return_value=_SessionErrorKind.AUTH,
            ),
            patch.dict(sys.modules, {"tidalapi": tidalapi}),
        ):
            client._get_session()
            self.assertFalse(session_file.is_file())
            self.assertFalse(client.is_logged_in())

    def test_refresh_attempted_when_check_login_false(self) -> None:
        session_file = Path(self._testMethodName) / "tidal-session.json"
        session_file.parent.mkdir(parents=True, exist_ok=True)
        _write_session_file(session_file)
        tidalapi, instance = _mock_tidalapi_session()
        instance.check_login.side_effect = [False, True, True]
        client = TidalClient(session_file)
        with (
            patch(
                "tunes_player.core.backends.tidal.client.tidalapi_available",
                return_value=True,
            ),
            patch.dict(sys.modules, {"tidalapi": tidalapi}),
        ):
            session = client._require_login()
            self.assertIs(session, instance)
            instance.token_refresh.assert_called_once_with("refresh-xyz")
            instance.save_session_to_file.assert_called()
            self.assertTrue(client.is_logged_in())

    def test_concurrent_get_session_no_file_deletion(self) -> None:
        session_file = Path(self._testMethodName) / "tidal-session.json"
        session_file.parent.mkdir(parents=True, exist_ok=True)
        _write_session_file(session_file)
        tidalapi, instance = _mock_tidalapi_session()
        gate = threading.Barrier(2)
        transient = requests.ConnectionError("offline")

        def check_login_side_effect() -> bool:
            gate.wait(timeout=5)
            if threading.current_thread().name == "transient":
                raise transient
            return True

        instance.check_login.side_effect = check_login_side_effect
        client = TidalClient(session_file)
        errors: list[BaseException] = []

        def worker(name: str) -> None:
            threading.current_thread().name = name
            try:
                with (
                    patch(
                        "tunes_player.core.backends.tidal.client.tidalapi_available",
                        return_value=True,
                    ),
                    patch.dict(sys.modules, {"tidalapi": tidalapi}),
                ):
                    if name == "transient":
                        try:
                            client._require_login()
                        except TidalUnavailableError as exc:
                            errors.append(exc)
                    else:
                        client._get_session()
            except Exception as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=worker, args=("success",)),
            threading.Thread(target=worker, args=("transient",)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        self.assertTrue(session_file.is_file())
        self.assertTrue(client.is_logged_in())


if __name__ == "__main__":
    unittest.main()
