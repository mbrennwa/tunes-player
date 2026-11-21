"""PlayerService with a remote MPD output (#60)."""

from __future__ import annotations

import tempfile
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import patch

from tests.fake_mpd import FakeMpdServer
from tunes_player.core.backends.playable import PlayableSource
from tunes_player.core.config import ConfigManager
from tunes_player.core.models import Source, Track
from tunes_player.core.release_quality import QUALITY_FILTER_CD, QUALITY_FILTER_HI_RES
from tunes_player.core.services import PlayerService
from tunes_player.core.volume import mpd_endpoint_id
from tunes_player.engines.mpd import MpdEngine


def _track(track_id: str = "local:file:one") -> Track:
    return Track(
        id=track_id,
        title="Track",
        artist_name="Artist",
        release_title="Album",
        source=Source.LOCAL,
        duration_sec=200.0,
    )


class RemoteOutputServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.server = FakeMpdServer()
        self.addCleanup(self.server.close)
        self.config_path = Path(self._tmp.name) / "config.json"
        config = ConfigManager(self.config_path)
        config.load()
        self.service = PlayerService(config=config)
        self.addCleanup(self._shutdown)
        self.music = Path(self._tmp.name) / "track.flac"
        self.music.write_bytes(b"fLaC" + b"\x00" * 500)
        self.errors: list[str | None] = []
        self.service.subscribe(self._on_event)
        self.remote = self.service.add_remote_output(
            "Den", self.server.host, self.server.port
        )
        assert self.remote is not None

    def _shutdown(self) -> None:
        self.service.shutdown()

    def _on_event(self, event: str) -> None:
        if event == "playback_error":
            self.errors.append(self.service.last_error())

    def _source(self, path: Path | None = None) -> PlayableSource:
        target = path or self.music
        return PlayableSource(
            uri=f"file://{target}", metadata=_track(), format_label="FLAC"
        )

    def _start_local_track(self, *, position: float = 0.0, playing: bool = True) -> None:
        self.service._current_track = _track()
        self.service._position_sec = position
        self.service._is_playing = playing
        self.service._playback_intended = playing

    def _select_remote(self, source: PlayableSource | None = None) -> None:
        with patch(
            "tunes_player.core.services.resolve_track",
            return_value=source or self._source(),
        ):
            self.service.set_output_sink(self.remote.id)

    # -- configuration -------------------------------------------------

    def test_add_validates_and_deduplicates(self) -> None:
        self.assertIsNone(self.service.add_remote_output("x", "", 6600))
        self.assertIsNone(self.service.add_remote_output("x", "host", 0))
        self.assertIsNone(self.service.add_remote_output("x", "host:1", 6600))
        before = len(self.service._config_manager.config.remote_outputs)
        self.service.add_remote_output("Den again", self.server.host, self.server.port)
        self.assertEqual(len(self.service._config_manager.config.remote_outputs), before)

    def test_remote_output_is_listed_for_selection(self) -> None:
        listed = self.service.list_output_sinks()
        self.assertIn(self.remote.id, [item.id for item in listed])

    def test_selecting_remote_keeps_local_sink_choice(self) -> None:
        cfg = self.service._config_manager.config
        cfg.output_sink_id = "pw:local-sink"
        self._select_remote()
        self.assertTrue(self.service.remote_output_active())
        self.assertEqual(self.service.active_output_id(), self.remote.id)
        self.assertEqual(cfg.output_sink_id, "pw:local-sink")
        self.assertEqual(cfg.remote_output_id, self.remote.id)
        reloaded = ConfigManager(self.config_path)
        reloaded.load()
        self.assertEqual(reloaded.config.remote_output_id, self.remote.id)

    def test_unknown_remote_id_is_ignored(self) -> None:
        self.service.set_output_sink(mpd_endpoint_id("elsewhere", 6600))
        self.assertFalse(self.service.remote_output_active())

    # -- volume --------------------------------------------------------

    def test_volume_is_fixed_for_remote_output(self) -> None:
        self._select_remote()
        self.assertFalse(self.service.volume_control_enabled())
        self.assertFalse(self.service._has_device_volume())
        self.assertEqual(self.service._volume_mode(), "fixed")
        self.service.set_volume(0.2)
        self.assertEqual(self.server.commands_named("setvol"), [])

    # -- playback ------------------------------------------------------

    def test_current_local_track_moves_to_remote_with_position(self) -> None:
        self._start_local_track(position=12.0, playing=True)
        self._select_remote()
        self.assertIsInstance(self.service._engine, MpdEngine)
        self.assertEqual(len(self.server.queue), 1)
        url = self.server.queue[0]
        self.assertTrue(url.startswith("http://127.0.0.1:"), url)
        self.assertEqual(self.server.commands_named("seek")[-1], 'seek "0" "12.000"')
        with urllib.request.urlopen(url, timeout=3) as response:
            self.assertEqual(response.read(), self.music.read_bytes())
        self.assertTrue(self.service._is_playing)
        state = self.service.get_playback_state()
        self.assertEqual(state.playback_note, "via MPD (Den)")
        self.assertFalse(state.bit_perfect_playback)
        self.assertEqual(state.volume_mode, "fixed")
        self.assertEqual(self.errors, [])

    def test_paused_track_stays_paused_on_remote(self) -> None:
        self._start_local_track(position=5.0, playing=False)
        self._select_remote()
        self.assertEqual(self.server.state, "pause")
        self.assertFalse(self.service._is_playing)

    def test_dash_manifest_is_reported_not_sent(self) -> None:
        manifest = Path(self._tmp.name) / "stream.mpd"
        manifest.write_text("<MPD/>")
        self._start_local_track()
        self._select_remote(self._source(manifest))
        self.assertEqual(self.server.queue, [])
        self.assertTrue(self.errors)
        self.assertIn("DASH", self.errors[-1] or "")
        self.assertFalse(self.service._is_playing)

    def test_stream_urls_pass_through_unchanged(self) -> None:
        self._start_local_track()
        stream = PlayableSource(
            uri="https://cdn.example/track.flac", metadata=_track(), format_label="FLAC"
        )
        self._select_remote(stream)
        self.assertEqual(self.server.queue, ["https://cdn.example/track.flac"])
        self.assertIsNone(self.service._remote_files)

    def test_unreachable_host_keeps_the_saved_selection(self) -> None:
        dead = FakeMpdServer()
        port = dead.port
        dead.close()
        output = self.service.add_remote_output("Dead", "127.0.0.1", port)
        assert output is not None
        self._start_local_track()
        with patch(
            "tunes_player.core.services.resolve_track", return_value=self._source()
        ):
            self.service.set_output_sink(output.id)
        self.assertIsNone(self.service._engine)
        self.assertEqual(
            self.service._config_manager.config.remote_output_id, output.id
        )
        self.assertTrue(self.errors)
        self.assertIn("Dead", self.errors[-1] or "")

    def test_busy_output_surfaces_a_friendly_message(self) -> None:
        self.server.busy = True
        self._start_local_track()
        self._select_remote()
        deadline = 3.0
        import time

        end = time.monotonic() + deadline
        while time.monotonic() < end and not any("busy" in (e or "") for e in self.errors):
            time.sleep(0.02)
            self.service.poll_playback()
        self.assertTrue(any("busy" in (e or "") for e in self.errors), self.errors)

    # -- leaving the remote output -------------------------------------

    def test_removing_the_active_output_returns_to_local(self) -> None:
        self._start_local_track()
        self._select_remote()
        self.assertIsNotNone(self.service._remote_files)
        with patch(
            "tunes_player.core.services.resolve_track", return_value=self._source()
        ):
            self.service.remove_remote_output(self.remote.id)
        self.assertFalse(self.service.remote_output_active())
        self.assertIsNone(self.service._config_manager.config.remote_output_id)
        self.assertIsNone(self.service._remote_files)
        self.assertEqual(self.service.list_output_sinks(), [])

    def test_output_change_drops_prepared_queue_entries(self) -> None:
        self.service._playlist_prepared = {"x": object()}  # type: ignore[assignment]
        generation = self.service._playlist_build_generation
        self._select_remote()
        self.assertEqual(self.service._playlist_prepared, {})
        self.assertGreater(self.service._playlist_build_generation, generation)

    # -- TIDAL tier ----------------------------------------------------

    def test_tidal_hi_res_is_capped_on_remote_only(self) -> None:
        from tunes_player.core.backends.resolve import PlaybackPreference

        tidal_id = "tidal:track:1"
        self.service._playlist_playback_preference = PlaybackPreference(
            QUALITY_FILTER_HI_RES
        )
        local = self.service._playback_preference_for_track(tidal_id)
        self.assertEqual(local.max_tier, QUALITY_FILTER_HI_RES)
        self._select_remote()
        capped = self.service._playback_preference_for_track(tidal_id)
        self.assertEqual(capped.max_tier, QUALITY_FILTER_CD)
        other = self.service._playback_preference_for_track("qobuz:track:1")
        self.assertEqual(other.max_tier, QUALITY_FILTER_HI_RES)

    def test_tidal_without_preference_is_capped_on_remote(self) -> None:
        self._select_remote()
        self.service._playlist_playback_preference = None
        capped = self.service._playback_preference_for_track("tidal:track:1")
        self.assertEqual(capped.max_tier, QUALITY_FILTER_CD)
        self.assertIsNone(self.service._playback_preference_for_track("local:file:x"))


if __name__ == "__main__":
    unittest.main()
