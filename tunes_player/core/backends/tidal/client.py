"""TIDAL API access via tidalapi."""

from __future__ import annotations

import base64
import concurrent.futures
import contextlib
import json
import logging
import threading
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

TidalRailFilter = Literal["all", "new_releases", "recommendations"]

from tunes_player.core.backends.playable import PlayableSource
from tunes_player.core.backends.tidal import convert, ids as tidal_ids
from tunes_player.core.backends.tidal.stream_quality import (
    negotiate_stream_payload,
    payload_audio_quality,
    playback_quality_candidates,
    quality_request_value,
    session_quality_for_subscription,
    subscription_allows_hi_res,
)
from tunes_player.core.home import (
    NEW_MUSIC_STREAMING_PER_SOURCE_LIMIT,
    SUGGESTIONS_SIMILAR_LIMIT,
    SUGGESTIONS_SIMILAR_TIMEOUT_SEC,
    SUGGESTIONS_STREAMING_PER_SOURCE_LIMIT,
    RecentlyAddedItem,
)
from tunes_player.core.models import Release, Track
from tunes_player.core.release_quality import (
    PlaybackPreference,
    playback_preference_from_shell,
)

if TYPE_CHECKING:
    import tidalapi
    from tidalapi.session import LinkLogin

log = logging.getLogger(__name__)

OAuthStatus = Literal["unavailable", "idle", "pending", "success", "failed"]


@contextlib.contextmanager
def _quiet_tidalapi_page_warnings():
    """tidalapi logs WARNING for home-page item types it does not parse (e.g. TASK)."""
    page_log = logging.getLogger("tidalapi.page")
    previous = page_log.level
    page_log.setLevel(logging.ERROR)
    try:
        yield
    finally:
        page_log.setLevel(previous)

_STREAM_CACHE_TTL_SEC = 120
_STREAM_RETRY_ATTEMPTS = 4
_NEW_RELEASE_TITLE_HINTS = (
    "new",
    "neu",
    "neue",
    "release",
    "releases",
    "erschein",
    "recent",
    "latest",
    "just added",
    "fresh",
    "out now",
    "spotlight",
    "dropped",
    "debut",
    "this week",
    "aktuell",
)
_RECOMMENDATION_TITLE_HINTS = (
    "for you",
    "recommended",
    "recommend",
    "mix",
    "daily",
    "picked",
    "because",
    "listen",
    "might like",
    "personal",
    "favourites",
    "favorites",
    "suggested",
    "inspired",
    "based on",
    "discover",
    "editorial",
    "curated",
    "stations",
    "station",
)


@dataclass(frozen=True, slots=True)
class _CollectedTidalObject:
    raw: object
    from_new_release_rail: bool
    from_recommendation_rail: bool


class TidalUnavailableError(RuntimeError):
    """Raised when TIDAL is unavailable or login failed."""


class _SessionErrorKind(Enum):
    TRANSIENT = "transient"
    AUTH = "auth"
    UNKNOWN = "unknown"


def _tidalapi_stored_field(data: dict[str, Any], key: str) -> str | None:
    entry = data.get(key)
    if isinstance(entry, dict):
        value = entry.get("data")
        if value is not None:
            return str(value)
    return None


def _session_file_has_credentials(session_file: Path) -> bool:
    if not session_file.is_file():
        return False
    try:
        raw = json.loads(session_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(raw, dict):
        return False
    if _tidalapi_stored_field(raw, "refresh_token"):
        return True
    access = _tidalapi_stored_field(raw, "access_token")
    session_id = _tidalapi_stored_field(raw, "session_id")
    return bool(access and session_id)


def _session_has_credentials(session: object) -> bool:
    if getattr(session, "refresh_token", None):
        return True
    return bool(getattr(session, "access_token", None) and getattr(session, "session_id", None))


def _classify_session_error(exc: BaseException) -> _SessionErrorKind:
    try:
        from tidalapi.exceptions import AuthenticationError

        if isinstance(exc, AuthenticationError):
            return _SessionErrorKind.AUTH
    except ImportError:
        pass
    try:
        import requests
        from requests.exceptions import HTTPError

        if isinstance(exc, (requests.ConnectionError, requests.Timeout, HTTPError)):
            if isinstance(exc, HTTPError) and exc.response is not None:
                status = exc.response.status_code
                if status >= 500:
                    return _SessionErrorKind.TRANSIENT
                if status in (401, 403):
                    return _SessionErrorKind.AUTH
            elif not isinstance(exc, HTTPError):
                return _SessionErrorKind.TRANSIENT
    except ImportError:
        pass
    if isinstance(exc, TimeoutError):
        return _SessionErrorKind.TRANSIENT
    return _SessionErrorKind.UNKNOWN


def tidalapi_available() -> bool:
    try:
        import tidalapi  # noqa: F401

        return True
    except ImportError:
        return False


class TidalClient:
    """Thin wrapper around tidalapi.Session for Tunes."""

    def __init__(self, session_file: Path, *, cache_dir: Path | None = None) -> None:
        self._session_file = session_file
        self._cache_dir = cache_dir or session_file.parent / "tidal-cache"
        self._session: tidalapi.Session | None = None
        self._oauth_executor: concurrent.futures.ThreadPoolExecutor | None = None
        self._oauth_future: concurrent.futures.Future[bool] | None = None
        self._oauth_link: LinkLogin | None = None
        self._oauth_error: str | None = None
        self._hi_res_entitled: bool | None = None
        self._stream_cache: dict[tuple[int, str], tuple[dict[str, Any], float]] = {}
        self._session_lock = threading.RLock()

    @property
    def session_file(self) -> Path:
        return self._session_file

    def is_available(self) -> bool:
        return tidalapi_available()

    def is_logged_in(self) -> bool:
        with self._session_lock:
            return self._has_stored_credentials_unlocked()

    def needs_lossless_relogin(self) -> bool:
        """Legacy device-link sessions cannot stream FLAC; PKCE sign-in is required."""
        with self._session_lock:
            session = self._get_session_unlocked()
            if session is None or not self._has_stored_credentials_unlocked():
                return False
            return not bool(getattr(session, "is_pkce", False))

    def stream_format_label(self, track_id: str | None = None) -> str:
        from tunes_player.core.playback_quality import (
            tidal_format_label,
            tidal_stream_format_label,
        )

        session = self._get_session()
        if session is None:
            return "Unknown format"
        if track_id:
            numeric = tidal_ids.parse_prefixed_id(track_id, "track")
            if numeric is not None:
                try:
                    tidal_track = session.track(numeric)
                    if tidal_track.audio_quality:
                        from tunes_player.core.backends.tidal.stream_quality import (
                            normalize_api_quality,
                            track_peak_quality,
                        )

                        catalog = normalize_api_quality(tidal_track.audio_quality)
                        if catalog != "HIGH" or track_peak_quality(tidal_track) >= 2:
                            return tidal_format_label(audio_quality=catalog)
                except Exception:
                    log.debug(
                        "Could not read TIDAL track quality for %s",
                        track_id,
                        exc_info=True,
                    )
        try:
            return tidal_stream_format_label(str(session.config.quality))
        except Exception:
            return "Unknown format"

    def account_label(self) -> str | None:
        session = self._get_session()
        if session is None or not self.is_logged_in():
            return None
        try:
            user = session.user
            if user is None:
                return None
            return user.username or user.first_name or str(user.id)
        except Exception:
            log.exception("Failed to read TIDAL user")
            return None

    def begin_pkce_login(self) -> str:
        """Return the TIDAL web login URL (PKCE). Complete with :meth:`complete_pkce_login`."""
        session = self._ensure_session()
        if session.check_login():
            raise TidalUnavailableError("Already signed in to TIDAL")
        self._stop_oauth(wait=False)
        self._oauth_error = None
        return session.pkce_login_url()

    def complete_pkce_login(self, redirect_url: str) -> None:
        """Finish PKCE login using the URL from the browser after sign-in."""
        session = self._ensure_session()
        url = redirect_url.strip()
        if not url:
            raise TidalUnavailableError(
                "Paste the full address from your browser’s location bar."
            )
        if "code=" not in url:
            raise TidalUnavailableError(
                "That address does not look like a TIDAL sign-in redirect "
                "(it should contain “code=”). Copy the whole location bar after signing in."
            )
        try:
            token = session.pkce_get_auth_token(url)
            session.process_auth_token(token, is_pkce_token=True)
        except Exception as exc:
            self._oauth_error = str(exc)
            log.exception("TIDAL PKCE login failed")
            raise TidalUnavailableError(str(exc)) from exc
        if not session.check_login():
            raise TidalUnavailableError("TIDAL login did not complete.")
        self._hi_res_entitled = None
        self._stream_cache.clear()
        self._activate_lossless_api_client(session)
        self._apply_preferred_stream_quality(session)
        self.save_session()
        self._oauth_error = None

    def begin_oauth(self) -> tuple[str, float]:
        """Start device-link login.

        Returns (verification URL, expires_in seconds).
        """
        session = self._ensure_session()
        if session.check_login():
            raise TidalUnavailableError("Already signed in to TIDAL")

        self._stop_oauth(wait=False)
        self._oauth_error = None
        link = session.get_link_login()
        self._oauth_link = link
        # tidalapi.login_oauth() uses a local ThreadPoolExecutor that can be GC'd
        # before process_link_login finishes — keep our own executor alive.
        self._oauth_executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="tunes-tidal-oauth",
        )
        self._oauth_future = self._oauth_executor.submit(session.process_link_login, link)
        return (
            _normalize_oauth_url(link.verification_uri_complete),
            float(link.expires_in),
        )

    def poll_oauth(self) -> OAuthStatus:
        if not tidalapi_available():
            return "unavailable"
        future = self._oauth_future
        if future is None:
            return "idle"
        if not future.done():
            return "pending"
        try:
            if not future.result(timeout=0):
                self._oauth_error = "TIDAL authorization was denied."
                self._stop_oauth(wait=True)
                return "failed"
        except TimeoutError:
            self._oauth_error = "Sign-in timed out. Try again and finish in the browser promptly."
            log.warning("TIDAL device login timed out")
            self._stop_oauth(wait=True)
            return "failed"
        except Exception as exc:
            self._oauth_error = str(exc)
            log.exception("TIDAL OAuth failed")
            self._stop_oauth(wait=True)
            return "failed"
        self._stop_oauth(wait=True)
        session = self._ensure_session()
        if not session.check_login():
            self._oauth_error = "TIDAL login did not complete."
            return "failed"
        self._activate_lossless_api_client(session)
        self._apply_preferred_stream_quality(session)
        self.save_session()
        return "success"

    def oauth_error_message(self) -> str | None:
        return self._oauth_error

    def save_session(self) -> None:
        with self._session_lock:
            session = self._session
            if session is None:
                return
            self._save_session_unlocked(session)

    def cancel_oauth(self) -> None:
        """Abort an in-progress device-link login."""
        self._stop_oauth(wait=False)

    def logout(self) -> None:
        self.cancel_oauth()
        with self._session_lock:
            self._hi_res_entitled = None
            self._stream_cache.clear()
            self._invalidate_session_unlocked()

    def _clear_stored_session(self) -> None:
        if self._session_file.is_file():
            try:
                self._session_file.unlink()
            except OSError:
                log.warning("Could not remove TIDAL session file %s", self._session_file)

    def _invalidate_session(self) -> None:
        """Forget in-memory and on-disk TIDAL credentials after confirmed auth failure."""
        with self._session_lock:
            self._invalidate_session_unlocked()

    def _invalidate_session_unlocked(self) -> None:
        self._clear_stored_session()
        self._hi_res_entitled = None
        self._stream_cache.clear()
        self._session = None

    def _has_stored_credentials_unlocked(self) -> bool:
        if not tidalapi_available():
            return False
        if self._session is not None and _session_has_credentials(self._session):
            return True
        return _session_file_has_credentials(self._session_file)

    def _save_session_unlocked(self, session: object) -> None:
        if not _session_has_credentials(session):
            return
        self._session_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            session.save_session_to_file(self._session_file)
        except Exception:
            log.warning("Could not persist TIDAL session", exc_info=True)

    def _finish_session_setup_unlocked(self, session: object) -> None:
        self._activate_lossless_api_client(session)
        if not bool(getattr(session, "is_pkce", False)):
            log.warning(
                "TIDAL session uses legacy device sign-in (320 kbps only); "
                "sign out and sign in again for lossless playback"
            )
        self._apply_preferred_stream_quality(session)
        self._save_session_unlocked(session)

    def _try_refresh_session_unlocked(self, session: object) -> bool:
        refresh_token = getattr(session, "refresh_token", None)
        if not refresh_token:
            return False
        try:
            refreshed = session.token_refresh(refresh_token)
        except Exception as exc:
            if _classify_session_error(exc) == _SessionErrorKind.AUTH:
                log.warning("TIDAL refresh token invalid; sign in again (%s)", exc)
                self._invalidate_session_unlocked()
            else:
                log.warning("TIDAL token refresh failed (%s)", exc)
            return False
        if not refreshed:
            return False
        try:
            if not session.check_login():
                return False
        except Exception as exc:
            if _classify_session_error(exc) == _SessionErrorKind.AUTH:
                log.warning("TIDAL session invalid after refresh; sign in again (%s)", exc)
                self._invalidate_session_unlocked()
            else:
                log.warning("TIDAL login check failed after refresh (%s)", exc)
            return False
        self._finish_session_setup_unlocked(session)
        return True

    def _session_is_live_unlocked(self, session: object) -> bool:
        try:
            if session.check_login():
                return True
            return self._try_refresh_session_unlocked(session)
        except Exception as exc:
            if _classify_session_error(exc) == _SessionErrorKind.AUTH:
                log.warning("TIDAL session invalid; sign in again (%s)", exc)
                self._invalidate_session_unlocked()
                return False
            log.warning("TIDAL session validation failed (%s)", exc)
            return False

    def search_releases(self, query: str, *, limit: int = 25) -> list[Release]:
        session = self._require_login()
        results = session.search(query, limit=limit)
        releases: list[Release] = []
        seen: set[str] = set()
        for item in results.get("albums", []):
            release = convert.release_stub_from_tidal(session, item)
            if release.id not in seen:
                seen.add(release.id)
                releases.append(release)
        for item in results.get("tracks", []):
            track = convert.track_from_tidal(session, item)
            if track.release_title and item.album is not None:
                release = convert.release_stub_from_tidal(session, item.album)
                if release.id not in seen:
                    seen.add(release.id)
                    releases.append(release)
        return releases[:limit]

    def list_new_release_items(
        self,
        *,
        limit: int = NEW_MUSIC_STREAMING_PER_SOURCE_LIMIT,
        within_days: int = 30,
    ) -> list[RecentlyAddedItem]:
        """Albums from TIDAL new-release style curated rails (expanded view-all lists)."""
        if not self.is_logged_in():
            return []
        session = self._require_login()
        items: list[RecentlyAddedItem] = []
        seen_album_ids: set[str] = set()
        try:
            import tidalapi.album as tidal_album_mod
            import tidalapi.media as tidal_media_mod

            collected = _collect_tidal_home_pages(
                session,
                include_explore=False,
                include_legacy_home=False,
                max_depth=2,
                rail_filter="new_releases",
            )

            for entry in collected:
                try:
                    for raw in _expand_tidal_mix_contents(entry.raw):
                        item = _recently_added_from_tidal_object(
                            session,
                            raw,
                            tidal_album_mod=tidal_album_mod,
                            tidal_media_mod=tidal_media_mod,
                            apply_date_filter=True,
                            within_days=within_days,
                        )
                        if item is None or item.release.id in seen_album_ids:
                            continue
                        seen_album_ids.add(item.release.id)
                        items.append(item)
                        if len(items) >= limit:
                            break
                    if len(items) >= limit:
                        break
                except Exception:
                    log.debug(
                        "Skipping TIDAL new-release item %s",
                        type(entry.raw).__name__,
                        exc_info=True,
                    )
        except Exception:
            log.exception("Failed to load TIDAL new releases")
            return []
        items.sort(key=lambda item: item.added_ns, reverse=True)
        return items[:limit]

    def list_suggestion_items(
        self,
        *,
        limit: int = SUGGESTIONS_STREAMING_PER_SOURCE_LIMIT,
    ) -> list[RecentlyAddedItem]:
        """Albums from TIDAL recommendation / for-you style modules (not new releases)."""
        if not self.is_logged_in():
            return []
        session = self._require_login()
        items: list[RecentlyAddedItem] = []
        seen_album_ids: set[str] = set()
        rank_base = time.time_ns()
        try:
            import tidalapi.album as tidal_album_mod
            import tidalapi.media as tidal_media_mod

            collected = _collect_tidal_home_pages(
                session,
                include_explore=False,
                include_legacy_home=False,
                max_depth=2,
                rail_filter="recommendations",
            )
            rank = 0
            for entry in collected:
                try:
                    for raw in _expand_tidal_mix_contents(entry.raw):
                        item = _recently_added_from_tidal_object(
                            session,
                            raw,
                            tidal_album_mod=tidal_album_mod,
                            tidal_media_mod=tidal_media_mod,
                            apply_date_filter=False,
                        )
                        if item is None or item.release.id in seen_album_ids:
                            continue
                        seen_album_ids.add(item.release.id)
                        items.append(
                            RecentlyAddedItem(
                                added_ns=rank_base - rank,
                                release=item.release,
                            ),
                        )
                        rank += 1
                        if len(items) >= limit:
                            return items
                except Exception:
                    log.debug(
                        "Skipping TIDAL suggestion item %s",
                        type(entry.raw).__name__,
                        exc_info=True,
                    )
        except Exception:
            log.exception("Failed to load TIDAL suggestions")
            return []
        return items[:limit]

    def list_similar_items(
        self,
        seed_track_id: str,
        *,
        limit: int = SUGGESTIONS_SIMILAR_LIMIT,
    ) -> list[RecentlyAddedItem]:
        """Albums similar to a TIDAL track (track radio)."""
        numeric = tidal_ids.parse_prefixed_id(seed_track_id, "track")
        if numeric is None or not self.is_logged_in():
            return []
        session = self._require_login()
        try:
            with concurrent.futures.ThreadPoolExecutor(
                max_workers=1,
                thread_name_prefix="tunes-tidal-radio",
            ) as executor:
                future = executor.submit(
                    _fetch_tidal_track_radio,
                    session,
                    numeric,
                    limit * 3,
                )
                radio_tracks = future.result(timeout=SUGGESTIONS_SIMILAR_TIMEOUT_SEC)
        except concurrent.futures.TimeoutError:
            log.warning(
                "TIDAL track radio timed out for %s after %.0fs",
                seed_track_id,
                SUGGESTIONS_SIMILAR_TIMEOUT_SEC,
            )
            return []
        except Exception as exc:
            # Track radio is optional for Suggestion; TIDAL often returns 5xx here.
            log.warning(
                "TIDAL track radio unavailable for %s: %s",
                seed_track_id,
                exc,
            )
            return []
        items: list[RecentlyAddedItem] = []
        seen: set[str] = set()
        rank_base = time.time_ns()
        for index, raw in enumerate(radio_tracks):
            if raw.album is None:
                continue
            release = convert.release_stub_from_tidal(session, raw.album)
            if release.id in seen:
                continue
            seen.add(release.id)
            items.append(RecentlyAddedItem(added_ns=rank_base - index, release=release))
            if len(items) >= limit:
                break
        return items

    def release_id_for_track(self, track_id: str) -> str | None:
        numeric = tidal_ids.parse_prefixed_id(track_id, "track")
        if numeric is None:
            return None
        try:
            session = self._require_login()
            tidal_track = session.track(numeric)
            if tidal_track.album is None:
                return None
            return tidal_ids.album_id(tidal_track.album.id)
        except Exception:
            log.debug("Could not resolve TIDAL release for track %s", track_id, exc_info=True)
            return None

    def get_release_summary(self, release_id: str) -> Release | None:
        """Release metadata for grids without loading every track."""
        numeric = tidal_ids.parse_prefixed_id(release_id, "album")
        if numeric is None:
            return None
        session = self._require_login()
        album = session.album(numeric)
        return convert.release_from_tidal(session, album)

    def get_release(self, release_id: str) -> Release | None:
        numeric = tidal_ids.parse_prefixed_id(release_id, "album")
        if numeric is None:
            return None
        session = self._require_login()
        album = session.album(numeric)
        tracks = list(album.tracks())
        duration = sum(float(t.duration or 0) for t in tracks)
        release = convert.release_from_tidal(
            session,
            album,
            owned_track_count=len(tracks),
            tracks=tracks,
        )
        if duration:
            from dataclasses import replace

            release = replace(release, duration_sec=duration)
        return release

    def get_release_tracks(self, release_id: str) -> list[Track]:
        numeric = tidal_ids.parse_prefixed_id(release_id, "album")
        if numeric is None:
            return []
        session = self._require_login()
        album = session.album(numeric)
        tracks: list[Track] = []
        for item in album.tracks():
            tracks.append(convert.track_from_tidal(session, item, album=album))
        return tracks

    def get_track(self, track_id: str) -> Track | None:
        numeric = tidal_ids.parse_prefixed_id(track_id, "track")
        if numeric is None:
            return None
        session = self._require_login()
        track = session.track(numeric)
        return convert.track_from_tidal(session, track)

    def queue_for_track(self, track_id: str) -> tuple[list[Track], int]:
        """Album queue for a track, or a single-track queue if no album context."""
        track = self.get_track(track_id)
        if track is None:
            return [], 0
        numeric = tidal_ids.parse_prefixed_id(track_id, "track")
        if numeric is None:
            return [track], 0
        session = self._require_login()
        tidal_track = session.track(numeric)
        if tidal_track.album is not None:
            album_id = tidal_ids.album_id(tidal_track.album.id)
            queue = self.get_release_tracks(album_id)
            index = next((i for i, item in enumerate(queue) if item.id == track_id), 0)
            return queue, index
        return [track], 0

    def resolve_playable(
        self,
        track_id: str,
        *,
        playback_preference: PlaybackPreference | None = None,
    ) -> PlayableSource | None:
        numeric = tidal_ids.parse_prefixed_id(track_id, "track")
        if numeric is None:
            return None
        session = self._require_login()
        track = session.track(numeric)
        metadata = convert.track_from_tidal(session, track)
        try:
            payload = self._fetch_stream_payload(
                session,
                numeric,
                track,
                playback_preference=playback_preference,
            )
            presentation = payload.get("assetPresentation")
            if presentation == "PREVIEW":
                raise TidalUnavailableError(
                    "TIDAL only returned a ~30s preview for this account. "
                    "Sign out, then sign in again (Settings → Sources → TIDAL). "
                    "A paid TIDAL subscription is required for full tracks."
                )
            manifest_b64 = payload.get("manifest")
            if not manifest_b64:
                log.warning("TIDAL stream payload missing manifest for track %s", numeric)
                return None
            playback_uri = self._playback_uri_from_manifest(
                track_id,
                manifest_b64=manifest_b64,
                manifest_mime=payload.get("manifestMimeType", ""),
            )
            if playback_uri is None:
                return None
        except TidalUnavailableError:
            raise
        except Exception:
            log.exception("Failed to resolve TIDAL stream for track %s", numeric)
            return None
        from tunes_player.core.playback_quality import (
            tidal_format_label_from_stream_payload,
            tidal_stream_file_metadata,
        )

        format_label = tidal_format_label_from_stream_payload(payload)
        return PlayableSource(
            uri=playback_uri,
            metadata=metadata,
            format_label=format_label,
            stream_metadata=tidal_stream_file_metadata(payload),
        )

    def _fetch_stream_payload(
        self,
        session: object,
        track_id: int,
        tidal_track: object,
        *,
        playback_preference: PlaybackPreference | None = None,
    ) -> dict[str, Any]:
        from tunes_player.core.backends.tidal.stream_quality import (
            cap_session_quality_for_preference,
        )

        preference = playback_preference or playback_preference_from_shell(frozenset())
        session_quality = cap_session_quality_for_preference(
            quality_request_value(session.config.quality),
            preference,
        )
        candidates = playback_quality_candidates(
            session_quality,
            tidal_track,
            preference=preference,
        )
        cache_key = (
            track_id,
            preference.max_tier,
            ",".join(candidates),
        )
        now = time.monotonic()
        cached = self._stream_cache.get(cache_key)
        if cached is not None:
            payload, expires_at = cached
            if now < expires_at:
                return payload
            del self._stream_cache[cache_key]

        payload, chosen = negotiate_stream_payload(
            candidates,
            lambda quality: self._request_stream_payload(session, track_id, quality),
            preference=preference,
        )
        from tunes_player.core.backends.tidal.stream_quality import (
            should_warn_hi_res_filter_miss,
            should_warn_lossy_stream_fallback,
        )

        resolved = payload_audio_quality(payload)
        if should_warn_hi_res_filter_miss(
            payload,
            tidal_track,
            preference=preference,
        ):
            log.warning(
                "TIDAL track %s: catalog hi-res but stream resolved to %s",
                track_id,
                resolved,
            )
        if should_warn_lossy_stream_fallback(candidates, resolved):
            log.warning(
                "TIDAL track %s is streaming at HIGH (320 kbps) after trying %s",
                track_id,
                ", ".join(candidates),
            )
        elif resolved != "HIGH":
            log.debug(
                "TIDAL track %s stream tier %s -> %s",
                track_id,
                chosen,
                resolved,
            )
        self._stream_cache[cache_key] = (payload, now + _STREAM_CACHE_TTL_SEC)
        return payload

    def _request_stream_payload(
        self,
        session: object,
        track_id: int,
        audio_quality: str,
    ) -> dict[str, Any]:
        from tidalapi.exceptions import TooManyRequests

        params = {
            "playbackmode": "STREAM",
            "audioquality": audio_quality,
            "assetpresentation": "FULL",
            "deviceType": "BROWSER",
            "platform": "WEB",
        }
        last_rate_limit: TooManyRequests | None = None
        for attempt in range(_STREAM_RETRY_ATTEMPTS):
            try:
                response = session.request.request(
                    "GET",
                    f"tracks/{track_id}/playbackinfopostpaywall",
                    params,
                )
                return response.json()
            except TooManyRequests as exc:
                last_rate_limit = exc
                if attempt + 1 >= _STREAM_RETRY_ATTEMPTS:
                    break
                if exc.retry_after > 0:
                    wait_sec = float(exc.retry_after)
                else:
                    wait_sec = min(2.0**attempt, 30.0)
                log.warning(
                    "TIDAL rate limited for track %s (attempt %s/%s), retry in %.0fs",
                    track_id,
                    attempt + 1,
                    _STREAM_RETRY_ATTEMPTS,
                    wait_sec,
                )
                time.sleep(wait_sec)
        if last_rate_limit is not None:
            raise TidalUnavailableError(
                "TIDAL rate limit reached. Wait a minute, then try again."
            ) from last_rate_limit
        raise RuntimeError("unreachable")

    def _playback_uri_from_manifest(
        self,
        track_id: str,
        *,
        manifest_b64: str,
        manifest_mime: str,
    ) -> str | None:
        manifest_text = base64.b64decode(manifest_b64).decode("utf-8")
        mime = manifest_mime.lower()
        if "bts" in mime or manifest_text.lstrip().startswith("{"):
            try:
                bts = json.loads(manifest_text)
            except json.JSONDecodeError:
                log.warning("Invalid TIDAL BTS manifest for %s", track_id)
                return None
            urls = bts.get("urls") or []
            if not urls:
                log.warning("TIDAL BTS manifest has no URLs for %s", track_id)
                return None
            return urls[0]
        mpd_path = self._write_mpd_manifest(track_id, manifest_text)
        return mpd_path.as_uri()

    def _write_mpd_manifest(self, track_id: str, mpd_xml: str) -> Path:
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        safe_id = track_id.replace(":", "_")
        path = self._cache_dir / f"{safe_id}.mpd"
        path.write_text(mpd_xml, encoding="utf-8")
        return path

    def _require_login(self) -> tidalapi.Session:
        with self._session_lock:
            if not self._has_stored_credentials_unlocked():
                raise TidalUnavailableError("Not signed in to TIDAL")
            session = self._get_session_unlocked()
            if session is None:
                raise TidalUnavailableError("Not signed in to TIDAL")
            if not self._session_is_live_unlocked(session):
                raise TidalUnavailableError(
                    "TIDAL is temporarily unavailable. Check your connection."
                )
            return session

    def _get_session(self) -> tidalapi.Session | None:
        with self._session_lock:
            return self._get_session_unlocked()

    def _get_session_unlocked(self) -> tidalapi.Session | None:
        if self._session is not None:
            return self._session
        if not tidalapi_available():
            return None
        import tidalapi

        self._session = tidalapi.Session()
        if not self._session_file.is_file():
            return self._session
        try:
            self._session.load_session_from_file(self._session_file)
            try:
                if self._session.check_login():
                    self._finish_session_setup_unlocked(self._session)
                else:
                    self._try_refresh_session_unlocked(self._session)
            except Exception as exc:
                if _classify_session_error(exc) == _SessionErrorKind.AUTH:
                    log.warning("TIDAL session expired or invalid; sign in again (%s)", exc)
                    self._invalidate_session_unlocked()
                    self._session = tidalapi.Session()
                else:
                    log.warning(
                        "TIDAL session validation failed (%s); keeping stored credentials",
                        exc,
                    )
        except Exception as exc:
            if _classify_session_error(exc) == _SessionErrorKind.AUTH:
                log.warning("TIDAL session expired or invalid; sign in again (%s)", exc)
                self._invalidate_session_unlocked()
                self._session = tidalapi.Session()
            else:
                log.warning(
                    "TIDAL session load failed (%s); keeping stored credentials",
                    exc,
                )
        return self._session

    @staticmethod
    def _activate_lossless_api_client(session: object) -> None:
        """Use the PKCE API client (required for FLAC / hi-res streams)."""
        enable = getattr(session, "client_enable_hires", None)
        if callable(enable):
            enable()

    def _apply_preferred_stream_quality(self, session: object) -> None:
        """Set session default to the best tier this subscription allows."""
        try:
            from tidalapi.media import Quality
        except ImportError:
            return
        self._activate_lossless_api_client(session)
        hi_res = self._subscription_allows_hi_res(session)
        tier = session_quality_for_subscription(hi_res_entitled=hi_res)
        if tier == "HI_RES_LOSSLESS":
            session.audio_quality = Quality.hi_res_lossless
        else:
            session.audio_quality = Quality.high_lossless
        log.debug("TIDAL session stream tier set to %s", tier)

    def _subscription_allows_hi_res(self, session: object) -> bool:
        if self._hi_res_entitled is not None:
            return self._hi_res_entitled
        self._hi_res_entitled = self._probe_subscription_hi_res(session)
        return self._hi_res_entitled

    @staticmethod
    def _probe_subscription_hi_res(session: object) -> bool:
        try:
            user = session.user
            user_id = getattr(user, "id", None)
            if user_id is None:
                return False
            response = session.request.basic_request(
                "GET",
                f"users/{user_id}/subscription",
            )
            if not response.ok:
                return False
            payload = response.json()
            if not isinstance(payload, dict):
                return False
            return subscription_allows_hi_res(payload)
        except Exception:
            log.debug("TIDAL subscription probe failed", exc_info=True)
            return False

    def _ensure_session(self) -> tidalapi.Session:
        session = self._get_session()
        if session is None:
            raise TidalUnavailableError(
                "TIDAL is unavailable (tidalapi could not be loaded)"
            )
        return session

    def _stop_oauth(self, *, wait: bool) -> None:
        future = self._oauth_future
        if future is not None and not future.done():
            future.cancel()
        self._oauth_future = None
        self._oauth_link = None
        executor = self._oauth_executor
        self._oauth_executor = None
        if executor is not None:
            executor.shutdown(wait=wait, cancel_futures=True)


def _tidal_album_added_ns(album: object) -> int:
    """When an album became 'new' on TIDAL (stream start), not original release year."""
    for attr in ("tidal_release_date", "user_date_added", "release_date"):
        dt = getattr(album, attr, None)
        if dt is not None:
            return int(dt.timestamp() * 1_000_000_000)
    return time.time_ns()


def _tidal_album_within_cutoff(album: object, cutoff_ns: int) -> bool:
    """Apply the window only when TIDAL provides a stream-start or added date."""
    for attr in ("tidal_release_date", "user_date_added"):
        dt = getattr(album, attr, None)
        if dt is not None:
            return int(dt.timestamp() * 1_000_000_000) >= cutoff_ns
    release = getattr(album, "release_date", None)
    if release is not None:
        return int(release.timestamp() * 1_000_000_000) >= cutoff_ns
    # Curated module placement without dates — keep the album.
    return True


def _category_is_new_release_rail(category: object) -> bool:
    title = (getattr(category, "title", None) or "").lower()
    if any(hint in title for hint in _NEW_RELEASE_TITLE_HINTS):
        return True
    mix_type = getattr(category, "mix_type", None)
    if mix_type is not None:
        name = mix_type.name if hasattr(mix_type, "name") else str(mix_type)
        if "new_release" in name.lower():
            return True
    return False


def _category_is_recommendation_rail(category: object) -> bool:
    if _category_is_new_release_rail(category):
        return False
    title = (getattr(category, "title", None) or "").lower()
    if any(hint in title for hint in _RECOMMENDATION_TITLE_HINTS):
        return True
    mix_type = getattr(category, "mix_type", None)
    if mix_type is not None:
        name = mix_type.name if hasattr(mix_type, "name") else str(mix_type)
        lowered = name.lower()
        if "new_release" not in lowered and "mix" in lowered:
            return True
    return False


def _fetch_tidal_track_radio(session: Any, track_id: int, limit: int) -> list[object]:
    tidal_track = session.track(track_id)
    return list(tidal_track.get_track_radio(limit=limit))


def _collect_tidal_home_pages(
    session: Any,
    *,
    include_explore: bool = True,
    include_legacy_home: bool = True,
    max_depth: int = 4,
    rail_filter: TidalRailFilter = "all",
) -> list[_CollectedTidalObject]:
    visited_pages: set[int] = set()
    collected: list[_CollectedTidalObject] = []
    with _quiet_tidalapi_page_warnings():
        pages: list[object] = [session.home()]
        optional_loaders: list[object] = []
        if include_legacy_home:
            optional_loaders.append(lambda: session.home(use_legacy_endpoint=True))
        if include_explore:
            optional_loaders.append(session.explore)
        optional_loaders.append(session.for_you)
        for loader in optional_loaders:
            try:
                pages.append(loader())
            except Exception:
                log.debug("TIDAL page %s unavailable", loader, exc_info=True)
        for page in pages:
            collected.extend(
                _collect_tidal_page_objects(
                    session,
                    page,
                    visited=visited_pages,
                    max_depth=max_depth,
                    rail_filter=rail_filter,
                ),
            )
    return collected


def _category_has_album_item(category: object) -> bool:
    items = getattr(category, "items", None) or []
    if not items:
        return False
    try:
        import tidalapi.album as tidal_album_mod
    except ImportError:
        return False
    return any(isinstance(item, tidal_album_mod.Album) for item in items)


def _should_expand_tidal_category(
    category: object,
    *,
    new_release_rail: bool = False,
    recommendation_rail: bool = False,
) -> bool:
    if getattr(category, "_more", None) is None:
        return False
    if (
        new_release_rail
        or recommendation_rail
        or _category_is_new_release_rail(category)
        or _category_is_recommendation_rail(category)
    ):
        return True
    return _category_has_album_item(category)


def _tidal_fetch_module_page(session: Any, category: object) -> object | None:
    """Fetch a module's view-all page (v1 pages/* or v2 home/* paths)."""
    more = getattr(category, "_more", None)
    if more is None:
        return None
    api_path = getattr(more, "api_path", None)
    if not api_path:
        return None
    return _tidal_fetch_page_at_path(session, str(api_path))


def _tidal_fetch_page_at_path(session: Any, api_path: str) -> object | None:
    page_root = getattr(session, "page", None)
    request = getattr(session, "request", None)
    config = getattr(session, "config", None)
    if page_root is None or request is None or config is None:
        return None

    path = api_path.strip()
    base_url = config.api_v1_location
    params: dict[str, str] = {"deviceType": "BROWSER"}

    if path.startswith("http://") or path.startswith("https://"):
        for marker, base in (
            ("api.tidal.com/v2/", config.api_v2_location),
            ("api.tidal.com/v1/", config.api_v1_location),
        ):
            if marker in path:
                path = path.split(marker, 1)[1]
                base_url = base
                break
    elif not path.startswith("pages/"):
        base_url = config.api_v2_location
        params["locale"] = getattr(session, "locale", "en_US") or "en_US"
        params["platform"] = "WEB"

    try:
        response = request.request("GET", path, params=params, base_url=base_url)
        return page_root.parse(response.json())
    except Exception:
        log.debug("TIDAL fetch failed for %s (base=%s)", path, base_url, exc_info=True)
        return None


def _collect_tidal_page_objects(
    session: Any,
    page: object,
    *,
    visited: set[int],
    depth: int = 0,
    max_depth: int = 4,
    new_release_rail: bool = False,
    recommendation_rail: bool = False,
    rail_filter: TidalRailFilter = "all",
) -> list[_CollectedTidalObject]:
    page_key = id(page)
    if page_key in visited:
        return []
    visited.add(page_key)

    collected: list[_CollectedTidalObject] = []
    categories = getattr(page, "categories", None)
    if categories:
        for category in categories:
            is_new = new_release_rail or _category_is_new_release_rail(category)
            is_rec = (
                recommendation_rail or _category_is_recommendation_rail(category)
            ) and not is_new
            if rail_filter == "new_releases" and not is_new:
                continue
            if rail_filter == "recommendations" and not is_rec:
                continue
            for item in getattr(category, "items", None) or []:
                if item is not None:
                    collected.append(_CollectedTidalObject(item, is_new, is_rec))
            if depth < max_depth and _should_expand_tidal_category(
                category,
                new_release_rail=is_new,
                recommendation_rail=is_rec,
            ):
                more_page = _tidal_fetch_module_page(session, category)
                if more_page is not None:
                    collected.extend(
                        _collect_tidal_page_objects(
                            session,
                            more_page,
                            visited=visited,
                            depth=depth + 1,
                            max_depth=max_depth,
                            new_release_rail=is_new,
                            recommendation_rail=is_rec,
                            rail_filter=rail_filter,
                        )
                    )
        return collected

    try:
        for item in page:
            if item is not None:
                if rail_filter == "new_releases" and not new_release_rail:
                    continue
                if rail_filter == "recommendations" and not recommendation_rail:
                    continue
                collected.append(
                    _CollectedTidalObject(item, new_release_rail, recommendation_rail),
                )
    except TypeError:
        pass
    return collected


def _expand_tidal_mix_contents(raw: object) -> list[object]:
    """Expand TIDAL mixes (e.g. NEW_RELEASE_MIX) into their tracks."""
    try:
        import tidalapi.mix as tidal_mix_mod
    except ImportError:
        return [raw]
    if isinstance(raw, (tidal_mix_mod.Mix, tidal_mix_mod.MixV2)):
        try:
            return list(raw.items())
        except Exception:
            log.debug("Could not load TIDAL mix items", exc_info=True)
            return []
    return [raw]


def _recently_added_from_tidal_object(
    session: Any,
    raw: object,
    *,
    tidal_album_mod: type,
    tidal_media_mod: type,
    apply_date_filter: bool,
    within_days: int = 30,
) -> RecentlyAddedItem | None:
    cutoff_ns = time.time_ns() - int(within_days * 86_400 * 1_000_000_000)
    if isinstance(raw, tidal_album_mod.Album):
        if apply_date_filter and not _tidal_album_within_cutoff(raw, cutoff_ns):
            return None
        added_ns = _tidal_album_added_ns(raw)
        return RecentlyAddedItem(
            added_ns=added_ns,
            release=convert.release_stub_from_tidal(session, raw),
        )
    if isinstance(raw, tidal_media_mod.Track):
        release_date = _tidal_track_release_date(raw)
        added_ns = (
            int(release_date.timestamp() * 1_000_000_000)
            if release_date is not None
            else time.time_ns()
        )
        if apply_date_filter and added_ns < cutoff_ns:
            return None
        if raw.album is None:
            return None
        return RecentlyAddedItem(
            added_ns=added_ns,
            release=convert.release_stub_from_tidal(session, raw.album),
        )
    page_item_type = getattr(raw, "type", None)
    if page_item_type == "ALBUM" and callable(getattr(raw, "get", None)):
        album = raw.get()
        return _recently_added_from_tidal_object(
            session,
            album,
            tidal_album_mod=tidal_album_mod,
            tidal_media_mod=tidal_media_mod,
            apply_date_filter=apply_date_filter,
            within_days=within_days,
        )
    return None


def _tidal_track_release_date(track: object) -> object | None:
    for attr in (
        "tidal_release_date",
        "stream_start_date",
        "date_added",
        "user_date_added",
    ):
        value = getattr(track, attr, None)
        if value is not None:
            return value
    album = getattr(track, "album", None)
    if album is not None:
        return (
            getattr(album, "available_release_date", None)
            or getattr(album, "release_date", None)
            or getattr(album, "tidal_release_date", None)
        )
    return None


def _normalize_oauth_url(url: str) -> str:
    value = url.strip()
    if not value or "://" in value:
        return value
    return f"https://{value}"
