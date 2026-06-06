"""Tests for Linux mount/network filesystem detection."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from tunes_player.platform.linux.mount_info import (
    clear_mount_cache,
    is_network_mount_path,
)


class MountInfoTests(unittest.TestCase):
    def tearDown(self) -> None:
        clear_mount_cache()

    def test_detects_nfs_mount_prefix(self) -> None:
        entries = ((Path("/mnt/nfs"), "nfs4"), (Path("/"), "ext4"))
        with (
            patch(
                "tunes_player.platform.linux.mount_info._mountinfo_entries",
                return_value=entries,
            ),
            patch(
                "tunes_player.platform.linux.mount_info._mount_entries",
                return_value=(),
            ),
            patch.object(Path, "resolve", lambda self: self),
        ):
            self.assertTrue(is_network_mount_path("/mnt/nfs/music/track.flac"))

    def test_local_path_not_network(self) -> None:
        entries = ((Path("/"), "ext4"), (Path("/home"), "ext4"))
        with (
            patch(
                "tunes_player.platform.linux.mount_info._mountinfo_entries",
                return_value=entries,
            ),
            patch(
                "tunes_player.platform.linux.mount_info._mount_entries",
                return_value=(),
            ),
            patch.object(Path, "resolve", lambda self: self),
        ):
            self.assertFalse(is_network_mount_path("/home/user/track.flac"))

    def test_cifs_counts_as_network(self) -> None:
        entries = ((Path("/mnt/share"), "cifs"),)
        with (
            patch(
                "tunes_player.platform.linux.mount_info._mountinfo_entries",
                return_value=entries,
            ),
            patch(
                "tunes_player.platform.linux.mount_info._mount_entries",
                return_value=(),
            ),
            patch.object(Path, "resolve", lambda self: self),
        ):
            self.assertTrue(is_network_mount_path("/mnt/share/album/track.flac"))

    def test_active_local_mount_wins_over_stale_autofs_nfs(self) -> None:
        """mountinfo shows ext4 even when /proc/mounts still lists autofs+nfs."""
        mount = Path("/home/user/music_gringotts")
        mountinfo = ((mount, "ext4"), (Path("/"), "ext4"))
        mounts = (
            (mount, "autofs"),
            (mount, "nfs"),
            (Path("/"), "ext4"),
        )
        with (
            patch(
                "tunes_player.platform.linux.mount_info._mountinfo_entries",
                return_value=mountinfo,
            ),
            patch(
                "tunes_player.platform.linux.mount_info._mount_entries",
                return_value=mounts,
            ),
            patch.object(Path, "resolve", lambda self: self),
        ):
            self.assertFalse(
                is_network_mount_path("/home/user/music_gringotts/album/track.flac")
            )

    def test_autofs_mount_prefers_nfs_when_active_mount_is_nfs(self) -> None:
        mount = Path("/home/user/music_gringotts")
        mountinfo = ((mount, "nfs4"), (Path("/"), "ext4"))
        mounts = (
            (mount, "autofs"),
            (mount, "nfs"),
            (Path("/"), "ext4"),
        )
        with (
            patch(
                "tunes_player.platform.linux.mount_info._mountinfo_entries",
                return_value=mountinfo,
            ),
            patch(
                "tunes_player.platform.linux.mount_info._mount_entries",
                return_value=mounts,
            ),
            patch.object(Path, "resolve", lambda self: self),
        ):
            self.assertTrue(
                is_network_mount_path("/home/user/music_gringotts/album/track.flac")
            )


if __name__ == "__main__":
    unittest.main()
