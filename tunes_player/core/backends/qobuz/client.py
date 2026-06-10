"""Qobuz API client (in-tree, no third-party SDK)."""

from __future__ import annotations

import hashlib
import json
import logging
import time
import urllib.error
from datetime import datetime, timezone
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from tunes_player.core.backends.playable import PlayableSource
from tunes_player.core.backends.qobuz import convert, ids as qobuz_ids
from tunes_player.core.home import (
    NEW_MUSIC_STREAMING_PER_SOURCE_LIMIT,
    SUGGESTIONS_STREAMING_PER_SOURCE_LIMIT,
    RecentlyAddedItem,
)
from tunes_player.core.models import Release, Track

log = logging.getLogger(__name__)

QOBUZ_BASE_URL = "https://www.qobuz.com/api.json/0.2"
DEFAULT_USER_AGENT = "Tunes-Player/0.1"

# format_id: 5=MP3 320, 6=16/44 FLAC, 7=24/96, 27=24/192
VALID_FORMAT_IDS = frozenset({5, 6, 7, 27})

# album/getFeatured types that surface new music (see Qobuz API / streamrip).
_NEW_RELEASE_FEATURE_TYPES = ("new-releases", "recent-releases")
_SUGGESTION_FEATURE_TYPES = ("editor-picks", "most-featured")


class QobuzUnavailableError(RuntimeError):
    """Raised when Qobuz is not configured, login failed, or stream unavailable."""


_QOBUZ_NO_RESULT_API_MESSAGE = "No result matching given argument"
_TRACK_UNAVAILABLE_MESSAGE = "This track isn't available for streaming on Qobuz."


def _user_facing_api_error(message: str, *, endpoint: str) -> str:
    if message == _QOBUZ_NO_RESULT_API_MESSAGE and endpoint.startswith("track/"):
        return _TRACK_UNAVAILABLE_MESSAGE
    return message


def sign_get_file_url(
    *,
    track_id: str,
    format_id: int,
    request_ts: float,
    app_secret: str,
) -> str:
    raw = (
        f"trackgetFileUrlformat_id{format_id}"
        f"intentstreamtrack_id{track_id}{request_ts}{app_secret}"
    )
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def _album_added_ns(album: dict[str, Any]) -> int:
    """Sort key for New Releases from Qobuz release/stream dates."""
    for key in ("release_date_stream", "released_at", "release_date_original"):
        raw = album.get(key)
        if isinstance(raw, str) and len(raw) >= 10:
            try:
                dt = datetime.strptime(raw[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
                return int(dt.timestamp() * 1_000_000_000)
            except ValueError:
                continue
    return time.time_ns()


class QobuzClient:
    """Thin Qobuz REST client for Tunes."""

    def __init__(
        self,
        session_file: Path,
        *,
        app_id: str | None = None,
        app_secret: str | None = None,
        format_id: int = 27,
    ) -> None:
        self._session_file = session_file
        self._app_id = (app_id or "").strip() or None
        self._app_secret = (app_secret or "").strip() or None
        if format_id not in VALID_FORMAT_IDS:
            format_id = 27
        self._format_id = format_id
        self._user_auth_token: str | None = None
        self._user_id: str | None = None
        self._account_login: str | None = None
        self._load_session()

    @property
    def session_file(self) -> Path:
        return self._session_file

    def is_configured(self) -> bool:
        return bool(self._app_id and self._app_secret)

    def is_logged_in(self) -> bool:
        return bool(self.is_configured() and self._user_auth_token)

    def account_label(self) -> str | None:
        if not self.is_logged_in():
            return None
        return self._account_login or self._user_id

    def set_credentials(self, app_id: str, app_secret: str, *, format_id: int | None = None) -> None:
        self._app_id = app_id.strip() or None
        self._app_secret = app_secret.strip() or None
        if format_id is not None and format_id in VALID_FORMAT_IDS:
            self._format_id = format_id

    def login(self, email: str, password: str) -> None:
        if not self.is_configured():
            raise QobuzUnavailableError(
                "Qobuz App ID and App Secret are required. Add them in Settings → Sources."
            )
        last_error: Exception | None = None
        for use_md5 in (False, True):
            try:
                self._login_attempt(email, password, md5_password=use_md5)
                self._save_session()
                return
            except QobuzUnavailableError as exc:
                last_error = exc
                if use_md5:
                    raise
        if last_error is not None:
            raise last_error
        raise QobuzUnavailableError("Qobuz login failed.")

    def logout(self) -> None:
        self._user_auth_token = None
        self._user_id = None
        self._account_login = None
        self._clear_session_file()

    def search_releases(self, query: str, *, limit: int = 25) -> list[Release]:
        self._require_login()
        releases: list[Release] = []
        seen: set[str] = set()
        album_resp = self._api_get("album/search", {"query": query, "limit": limit})
        albums = album_resp.get("albums", {})
        for item in albums.get("items") or []:
            if not isinstance(item, dict):
                continue
            release = convert.release_from_qobuz(
                self._enrich_qobuz_album_quality_metadata(item),
            )
            if release.id not in seen:
                seen.add(release.id)
                releases.append(release)
        track_resp = self._api_get("track/search", {"query": query, "limit": limit})
        tracks = track_resp.get("tracks", {})
        for item in tracks.get("items") or []:
            if not isinstance(item, dict):
                continue
            album = item.get("album")
            if not isinstance(album, dict):
                continue
            release = convert.release_from_qobuz(
                self._enrich_qobuz_album_quality_metadata(album),
            )
            if release.id not in seen:
                seen.add(release.id)
                releases.append(release)
        return releases[:limit]

    def _enrich_qobuz_album_quality_metadata(
        self,
        album: dict[str, Any],
    ) -> dict[str, Any]:
        """Merge album/get quality fields when search JSON omits hi-res availability."""
        from tunes_player.core.release_quality import (
            QUALITY_FILTER_HI_RES,
            tiers_from_qobuz_album,
        )

        if QUALITY_FILTER_HI_RES in tiers_from_qobuz_album(album):
            return album
        album_id = album.get("id") or album.get("qobuz_id")
        if album_id is None:
            return album
        summary = self._fetch_album_summary(str(album_id))
        if not isinstance(summary, dict):
            return album
        merged = dict(album)
        for key in (
            "hires",
            "hires_streamable",
            "maximum_bit_depth",
            "maximum_sampling_rate",
            "maximum_technical_specifications",
        ):
            value = summary.get(key)
            if value is not None:
                merged[key] = value
        summary_tracks = summary.get("tracks")
        if isinstance(summary_tracks, dict):
            items = summary_tracks.get("items")
            if isinstance(items, list) and items:
                merged_tracks = merged.get("tracks")
                if not isinstance(merged_tracks, dict):
                    merged["tracks"] = {"items": items[:5]}
                else:
                    existing = list(merged_tracks.get("items") or [])
                    if not existing:
                        merged_tracks = dict(merged_tracks)
                        merged_tracks["items"] = items[:5]
                        merged["tracks"] = merged_tracks
        return merged

    def list_new_release_items(
        self,
        *,
        limit: int = NEW_MUSIC_STREAMING_PER_SOURCE_LIMIT,
        within_days: int = 30,
    ) -> list[RecentlyAddedItem]:
        """Albums from Qobuz new-release featured rails."""
        if not self.is_logged_in():
            return []
        cutoff_ns = time.time_ns() - int(within_days * 86_400 * 1_000_000_000)
        items: list[RecentlyAddedItem] = []
        seen: set[str] = set()
        page_size = 100
        try:
            for feature_type in _NEW_RELEASE_FEATURE_TYPES:
                offset = 0
                while len(items) < limit:
                    data = self._api_get(
                        "album/getFeatured",
                        {
                            "type": feature_type,
                            "limit": min(page_size, limit - len(items)),
                            "offset": offset,
                        },
                    )
                    albums = data.get("albums") if isinstance(data, dict) else None
                    if not isinstance(albums, dict):
                        break
                    batch = albums.get("items") or []
                    if not batch:
                        break
                    batch_has_recent = False
                    for raw in batch:
                        if not isinstance(raw, dict):
                            continue
                        added_ns = _album_added_ns(raw)
                        if added_ns < cutoff_ns:
                            continue
                        batch_has_recent = True
                        release = convert.release_from_qobuz(raw)
                        if release.id in seen:
                            continue
                        seen.add(release.id)
                        items.append(RecentlyAddedItem(added_ns=added_ns, release=release))
                        if len(items) >= limit:
                            break
                    if not batch_has_recent:
                        break
                    if len(batch) < page_size:
                        break
                    offset += page_size
        except QobuzUnavailableError:
            raise
        except Exception:
            log.exception("Failed to load Qobuz new releases")
            return []
        items.sort(key=lambda item: item.added_ns, reverse=True)
        return items[:limit]

    def list_suggestion_items(
        self,
        *,
        limit: int = SUGGESTIONS_STREAMING_PER_SOURCE_LIMIT,
    ) -> list[RecentlyAddedItem]:
        """Albums from Qobuz editorial / featured rails (not new releases)."""
        if not self.is_logged_in():
            return []
        items: list[RecentlyAddedItem] = []
        seen: set[str] = set()
        rank_base = time.time_ns()
        rank = 0
        page_size = 100
        try:
            for feature_type in _SUGGESTION_FEATURE_TYPES:
                offset = 0
                while len(items) < limit:
                    data = self._api_get(
                        "album/getFeatured",
                        {
                            "type": feature_type,
                            "limit": min(page_size, limit - len(items)),
                            "offset": offset,
                        },
                    )
                    albums = data.get("albums") if isinstance(data, dict) else None
                    if not isinstance(albums, dict):
                        break
                    batch = albums.get("items") or []
                    if not batch:
                        break
                    for raw in batch:
                        if not isinstance(raw, dict):
                            continue
                        release = convert.release_from_qobuz(raw)
                        if release.id in seen:
                            continue
                        seen.add(release.id)
                        items.append(
                            RecentlyAddedItem(added_ns=rank_base - rank, release=release),
                        )
                        rank += 1
                        if len(items) >= limit:
                            break
                    if len(batch) < page_size:
                        break
                    offset += page_size
        except QobuzUnavailableError:
            raise
        except Exception:
            log.exception("Failed to load Qobuz suggestions")
            return []
        return items[:limit]

    def release_id_for_track(self, track_id: str) -> str | None:
        numeric = qobuz_ids.parse_prefixed_id(track_id, "track")
        if numeric is None:
            return None
        try:
            self._require_login()
            data = self._api_get("track/get", {"track_id": numeric})
            album_obj = data.get("album") if isinstance(data, dict) else None
            if isinstance(album_obj, dict) and album_obj.get("id") is not None:
                return qobuz_ids.album_id(album_obj["id"])
        except Exception:
            log.debug("Could not resolve Qobuz release for track %s", track_id, exc_info=True)
        return None

    def get_release_summary(self, release_id: str) -> Release | None:
        """Release metadata for grids without loading every track page."""
        album_id = qobuz_ids.parse_prefixed_id(release_id, "album")
        if album_id is None:
            return None
        self._require_login()
        album = self._fetch_album_summary(album_id)
        if album is None:
            return None
        return convert.release_from_qobuz(album)

    def get_release(self, release_id: str) -> Release | None:
        album_id = qobuz_ids.parse_prefixed_id(release_id, "album")
        if album_id is None:
            return None
        self._require_login()
        album = self._fetch_album(album_id)
        if album is None:
            return None
        tracks = self._album_track_items(album)
        return convert.release_from_qobuz(album, owned_track_count=len(tracks))

    def get_release_tracks(self, release_id: str) -> list[Track]:
        album_id = qobuz_ids.parse_prefixed_id(release_id, "album")
        if album_id is None:
            return []
        self._require_login()
        album = self._fetch_album(album_id)
        if album is None:
            return []
        return [
            convert.track_from_qobuz(item, album=album)
            for item in self._album_track_items(album)
            if isinstance(item, dict)
        ]

    def get_track(self, track_id: str) -> Track | None:
        numeric = qobuz_ids.parse_prefixed_id(track_id, "track")
        if numeric is None:
            return None
        self._require_login()
        data = self._api_get("track/get", {"track_id": numeric})
        if not isinstance(data, dict):
            return None
        return convert.track_from_qobuz(data)

    def queue_for_track(self, track_id: str) -> tuple[list[Track], int]:
        track = self.get_track(track_id)
        if track is None:
            return [], 0
        numeric = qobuz_ids.parse_prefixed_id(track_id, "track")
        data = self._api_get("track/get", {"track_id": numeric})
        album_obj = data.get("album") if isinstance(data, dict) else None
        if isinstance(album_obj, dict) and album_obj.get("id") is not None:
            album_id = qobuz_ids.album_id(album_obj["id"])
            queue = self.get_release_tracks(album_id)
            index = next((i for i, item in enumerate(queue) if item.id == track_id), 0)
            return queue, index
        return [track], 0

    def resolve_playable(
        self,
        track_id: str,
        *,
        playback_quality_policy: object | None = None,
    ) -> PlayableSource | None:
        from tunes_player.core.release_quality import (
            PlaybackQualityPolicy,
            playback_policy_for_play,
            qobuz_format_id_for_policy,
        )

        numeric = qobuz_ids.parse_prefixed_id(track_id, "track")
        if numeric is None:
            return None
        self._require_login()
        data = self._api_get("track/get", {"track_id": numeric})
        if not isinstance(data, dict):
            return None
        metadata = convert.track_from_qobuz(data)
        policy = (
            playback_quality_policy
            if isinstance(playback_quality_policy, PlaybackQualityPolicy)
            else playback_policy_for_play(
                enabled_quality_tiers=frozenset(),
                release=None,
            )
        )

        format_id = qobuz_format_id_for_policy(
            config_format_id=self._format_id,
            policy=policy,
        )
        stream = self._get_file_url(
            numeric,
            playback_quality_policy=policy,
        )
        from tunes_player.core.playback_quality import (
            qobuz_format_label_from_stream,
            qobuz_stream_file_metadata,
        )

        format_label = qobuz_format_label_from_stream(
            stream,
            fallback_format_id=format_id,
        )
        stream_metadata = qobuz_stream_file_metadata(stream)
        url = stream.get("url")
        if not url:
            restrictions = stream.get("restrictions")
            if isinstance(restrictions, list) and restrictions:
                code = restrictions[0].get("code", "restricted") if isinstance(restrictions[0], dict) else "restricted"
                raise QobuzUnavailableError(
                    f"Qobuz will not stream this track ({code}). "
                    "Check your subscription and account region."
                )
            raise QobuzUnavailableError("Qobuz did not return a stream URL for this track.")
        return PlayableSource(
            uri=str(url),
            metadata=metadata,
            format_label=format_label,
            stream_metadata=stream_metadata,
        )

    def _require_login(self) -> None:
        if not self.is_logged_in():
            raise QobuzUnavailableError("Sign in to Qobuz in Settings → Sources.")

    def _login_attempt(self, email: str, password: str, *, md5_password: bool) -> None:
        pwd = hashlib.md5(password.encode("utf-8")).hexdigest() if md5_password else password
        params = {
            "email": email.strip(),
            "password": pwd,
            "app_id": self._app_id,
        }
        data = self._api_get("user/login", params, authenticated=False)
        token = data.get("user_auth_token")
        if not token:
            raise QobuzUnavailableError(data.get("message") or "Qobuz login failed.")
        self._user_auth_token = str(token)
        user = data.get("user")
        if isinstance(user, dict):
            self._user_id = str(user.get("id", "")) or None
            self._account_login = (
                str(user.get("display_name") or user.get("login") or "") or None
            )
        credential = data.get("credential")
        if isinstance(credential, dict):
            params_block = credential.get("parameters")
            if isinstance(params_block, dict) and not params_block:
                raise QobuzUnavailableError(
                    "This Qobuz account cannot stream (free or inactive subscription)."
                )

    def _get_file_url(
        self,
        track_id: str,
        *,
        playback_quality_policy: object | None = None,
    ) -> dict[str, Any]:
        from tunes_player.core.release_quality import (
            PlaybackQualityPolicy,
            playback_policy_for_play,
            qobuz_format_id_for_policy,
        )

        policy = (
            playback_quality_policy
            if isinstance(playback_quality_policy, PlaybackQualityPolicy)
            else playback_policy_for_play(
                enabled_quality_tiers=frozenset(),
                release=None,
            )
        )
        format_id = qobuz_format_id_for_policy(
            config_format_id=self._format_id,
            policy=policy,
        )
        request_ts = time.time()
        request_sig = sign_get_file_url(
            track_id=track_id,
            format_id=format_id,
            request_ts=request_ts,
            app_secret=self._app_secret or "",
        )
        params = {
            "track_id": track_id,
            "format_id": str(format_id),
            "intent": "stream",
            "request_ts": str(request_ts),
            "request_sig": request_sig,
        }
        return self._api_get("track/getFileUrl", params)

    def _fetch_album_summary(self, album_id: str) -> dict[str, Any] | None:
        data = self._api_get(
            "album/get",
            {"album_id": album_id, "limit": 1, "offset": 0},
        )
        return data if isinstance(data, dict) else None

    def _fetch_album(self, album_id: str) -> dict[str, Any] | None:
        data = self._api_get(
            "album/get",
            {"album_id": album_id, "limit": 500, "offset": 0},
        )
        if not isinstance(data, dict):
            return None
        tracks = data.get("tracks")
        if not isinstance(tracks, dict):
            return data
        total = int(tracks.get("total") or 0)
        items = list(tracks.get("items") or [])
        offset = int(tracks.get("offset") or 0)
        limit = int(tracks.get("limit") or 500)
        while offset + limit < total:
            offset += limit
            page = self._api_get(
                "album/get",
                {"album_id": album_id, "limit": limit, "offset": offset},
            )
            page_tracks = page.get("tracks") if isinstance(page, dict) else None
            if isinstance(page_tracks, dict):
                items.extend(page_tracks.get("items") or [])
            else:
                break
        data = dict(data)
        data["tracks"] = {"total": total, "limit": limit, "offset": 0, "items": items}
        return data

    def _album_track_items(self, album: dict[str, Any]) -> list[Any]:
        tracks = album.get("tracks")
        if isinstance(tracks, dict):
            return list(tracks.get("items") or [])
        return []

    def _api_get(
        self,
        endpoint: str,
        params: dict[str, Any] | None = None,
        *,
        authenticated: bool = True,
    ) -> dict[str, Any]:
        query: dict[str, str] = {"app_id": self._app_id or ""}
        if params:
            for key, value in params.items():
                if value is not None:
                    query[key] = str(value)
        url = f"{QOBUZ_BASE_URL}/{endpoint}?{urllib.parse.urlencode(query)}"
        headers = {
            "User-Agent": DEFAULT_USER_AGENT,
            "Accept": "application/json",
        }
        if authenticated and self._user_auth_token:
            headers["X-User-Auth-Token"] = self._user_auth_token
        if self._app_id:
            headers["X-App-Id"] = self._app_id
        req = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                body = resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            raw_message = self._error_message_from_http(exc)
            message = _user_facing_api_error(raw_message, endpoint=endpoint)
            if message != raw_message:
                log.debug("Qobuz API error on %s: %s", endpoint, raw_message)
            if exc.code in (401, 400) and endpoint == "user/login":
                raise QobuzUnavailableError(message) from exc
            raise QobuzUnavailableError(message) from exc
        except urllib.error.URLError as exc:
            raise QobuzUnavailableError(f"Network error talking to Qobuz: {exc.reason}") from exc
        try:
            data = json.loads(body)
        except json.JSONDecodeError as exc:
            raise QobuzUnavailableError("Invalid response from Qobuz.") from exc
        if not isinstance(data, dict):
            raise QobuzUnavailableError("Unexpected Qobuz response.")
        if data.get("status") == "error":
            raw_message = str(data.get("message") or "Qobuz API error.")
            message = _user_facing_api_error(raw_message, endpoint=endpoint)
            if message != raw_message:
                log.debug("Qobuz API error on %s: %s", endpoint, raw_message)
            raise QobuzUnavailableError(message)
        return data

    @staticmethod
    def _error_message_from_http(exc: urllib.error.HTTPError) -> str:
        try:
            payload = json.loads(exc.read().decode("utf-8"))
            if isinstance(payload, dict) and payload.get("message"):
                return str(payload["message"])
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            pass
        return f"Qobuz request failed (HTTP {exc.code})."

    def _load_session(self) -> None:
        if not self._session_file.is_file():
            return
        try:
            raw = json.loads(self._session_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            log.warning("Could not read Qobuz session file %s", self._session_file)
            return
        if not isinstance(raw, dict):
            return
        token = raw.get("user_auth_token")
        if token:
            self._user_auth_token = str(token)
        uid = raw.get("user_id")
        if uid is not None:
            self._user_id = str(uid)
        login = raw.get("login")
        if login:
            self._account_login = str(login)

    def _save_session(self) -> None:
        if not self._user_auth_token:
            return
        self._session_file.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "user_auth_token": self._user_auth_token,
            "user_id": self._user_id,
            "login": self._account_login,
        }
        self._session_file.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    def _clear_session_file(self) -> None:
        if self._session_file.is_file():
            try:
                self._session_file.unlink()
            except OSError:
                log.warning("Could not remove Qobuz session file %s", self._session_file)
