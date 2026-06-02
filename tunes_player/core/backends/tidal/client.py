"""TIDAL API access via tidalapi (optional dependency)."""

from __future__ import annotations

import base64
import concurrent.futures
import json
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from tunes_player.core.backends.playable import PlayableSource
from tunes_player.core.backends.tidal import convert, ids as tidal_ids
from tunes_player.core.home import RecentlyAddedItem
from tunes_player.core.models import Release, Track

if TYPE_CHECKING:
    import tidalapi
    from tidalapi.session import LinkLogin

log = logging.getLogger(__name__)

OAuthStatus = Literal["unavailable", "idle", "pending", "success", "failed"]

_STREAM_CACHE_TTL_SEC = 120
_STREAM_RETRY_ATTEMPTS = 4


class TidalUnavailableError(RuntimeError):
    """Raised when tidalapi is not installed or login failed."""


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
        self._stream_cache: dict[tuple[int, str], tuple[dict[str, Any], float]] = {}

    @property
    def session_file(self) -> Path:
        return self._session_file

    def is_available(self) -> bool:
        return tidalapi_available()

    def is_logged_in(self) -> bool:
        session = self._get_session()
        if session is None:
            return False
        try:
            return bool(session.check_login())
        except Exception as exc:
            log.warning("TIDAL login check failed: %s", exc)
            self._drop_session()
            return False

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
            raise TidalUnavailableError("Paste the full URL from your browser.")
        if "code=" not in url:
            raise TidalUnavailableError(
                "That URL does not look like a TIDAL redirect (no authorization code)."
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
        self.save_session()
        return "success"

    def oauth_error_message(self) -> str | None:
        return self._oauth_error

    def save_session(self) -> None:
        session = self._get_session()
        if session is None:
            return
        self._session_file.parent.mkdir(parents=True, exist_ok=True)
        session.save_session_to_file(self._session_file)

    def cancel_oauth(self) -> None:
        """Abort an in-progress device-link login."""
        self._stop_oauth(wait=False)

    def logout(self) -> None:
        self.cancel_oauth()
        self._drop_session()

    def _clear_stored_session(self) -> None:
        if self._session_file.is_file():
            try:
                self._session_file.unlink()
            except OSError:
                log.warning("Could not remove TIDAL session file %s", self._session_file)

    def _drop_session(self) -> None:
        """Forget in-memory and on-disk TIDAL credentials."""
        self._clear_stored_session()
        self._session = None

    def search_releases(self, query: str, *, limit: int = 25) -> list[Release]:
        session = self._require_login()
        results = session.search(query, limit=limit)
        releases: list[Release] = []
        seen: set[str] = set()
        for item in results.get("albums", []):
            release = convert.release_from_tidal(session, item)
            if release.id not in seen:
                seen.add(release.id)
                releases.append(release)
        for item in results.get("tracks", []):
            track = convert.track_from_tidal(session, item)
            if track.album_title and item.album is not None:
                release = convert.release_from_tidal(session, item.album)
                if release.id not in seen:
                    seen.add(release.id)
                    releases.append(release)
        return releases[:limit]

    def list_new_release_items(
        self,
        *,
        within_days: int = 30,
        limit: int = 80,
    ) -> list[RecentlyAddedItem]:
        """New-release albums and tracks from the TIDAL home page."""
        if not self.is_logged_in():
            return []
        session = self._require_login()
        cutoff_ns = time.time_ns() - int(within_days * 86_400 * 1_000_000_000)
        items: list[RecentlyAddedItem] = []
        try:
            import tidalapi.album as tidal_album_mod
            import tidalapi.media as tidal_media_mod

            page = session.home()
            for raw in page:
                try:
                    added_ns = time.time_ns()
                    if isinstance(raw, tidal_album_mod.Album):
                        release = (
                            getattr(raw, "available_release_date", None)
                            or getattr(raw, "release_date", None)
                            or getattr(raw, "tidal_release_date", None)
                        )
                        if release is not None:
                            added_ns = int(release.timestamp() * 1_000_000_000)
                        if added_ns < cutoff_ns:
                            continue
                        tidal_release = convert.release_from_tidal(session, raw)
                        items.append(
                            RecentlyAddedItem(added_ns=added_ns, release=tidal_release)
                        )
                    elif isinstance(raw, tidal_media_mod.Track):
                        release_date = _tidal_track_release_date(raw)
                        if release_date is not None:
                            added_ns = int(release_date.timestamp() * 1_000_000_000)
                        if added_ns < cutoff_ns:
                            continue
                        if raw.album is None:
                            continue
                        tidal_release = convert.release_from_tidal(session, raw.album)
                        items.append(
                            RecentlyAddedItem(added_ns=added_ns, release=tidal_release)
                        )
                except Exception:
                    log.debug(
                        "Skipping TIDAL home item %s",
                        type(raw).__name__,
                        exc_info=True,
                    )
        except Exception:
            log.exception("Failed to load TIDAL new releases")
            return []
        items.sort(key=lambda item: item.added_ns, reverse=True)
        return items[:limit]

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
        )
        return Release(
            id=release.id,
            title=release.title,
            artist_name=release.artist_name,
            source=release.source,
            track_count=len(tracks),
            expected_track_count=release.expected_track_count,
            completeness=release.completeness,
            release_type=release.release_type,
            year=release.year,
            genre=release.genre,
            art_uri=release.art_uri,
            duration_sec=duration or None,
        )

    def get_album(self, album_id: str) -> Release | None:
        return self.get_release(album_id)

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

    def get_album_tracks(self, album_id: str) -> list[Track]:
        return self.get_release_tracks(album_id)

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
            queue = self.get_album_tracks(album_id)
            index = next((i for i, item in enumerate(queue) if item.id == track_id), 0)
            return queue, index
        return [track], 0

    def resolve_playable(self, track_id: str) -> PlayableSource | None:
        numeric = tidal_ids.parse_prefixed_id(track_id, "track")
        if numeric is None:
            return None
        session = self._require_login()
        track = session.track(numeric)
        metadata = convert.track_from_tidal(session, track)
        try:
            payload = self._fetch_stream_payload(session, numeric)
            presentation = payload.get("assetPresentation")
            if presentation == "PREVIEW":
                raise TidalUnavailableError(
                    "TIDAL only returned a ~30s preview for this account. "
                    "Sign out, then sign in again with the device link (not PKCE). "
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
        return PlayableSource(uri=playback_uri, metadata=metadata)

    def _fetch_stream_payload(self, session: object, track_id: int) -> dict[str, Any]:
        quality = str(session.config.quality)
        cache_key = (track_id, quality)
        now = time.monotonic()
        cached = self._stream_cache.get(cache_key)
        if cached is not None:
            payload, expires_at = cached
            if now < expires_at:
                return payload
            del self._stream_cache[cache_key]

        payload = self._request_stream_payload(session, track_id)
        self._stream_cache[cache_key] = (payload, now + _STREAM_CACHE_TTL_SEC)
        return payload

    def _request_stream_payload(self, session: object, track_id: int) -> dict[str, Any]:
        from tidalapi.exceptions import TooManyRequests

        params = {
            "playbackmode": "STREAM",
            "audioquality": session.config.quality,
            "assetpresentation": "FULL",
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
        if not self.is_logged_in():
            raise TidalUnavailableError("Not signed in to TIDAL")
        session = self._get_session()
        if session is None:
            raise TidalUnavailableError("Not signed in to TIDAL")
        return session

    def _get_session(self) -> tidalapi.Session | None:
        if self._session is not None:
            return self._session
        if not tidalapi_available():
            return None
        import tidalapi

        self._session = tidalapi.Session()
        if self._session_file.is_file():
            try:
                self._session.load_session_from_file(self._session_file)
            except Exception as exc:
                log.warning("TIDAL session expired or invalid; sign in again (%s)", exc)
                self._clear_stored_session()
                self._session = tidalapi.Session()
        return self._session

    def _ensure_session(self) -> tidalapi.Session:
        session = self._get_session()
        if session is None:
            raise TidalUnavailableError(
                "tidalapi is not installed. Install with: pip install 'tunes-player[streaming]'"
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
