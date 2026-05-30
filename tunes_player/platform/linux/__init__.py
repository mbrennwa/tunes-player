"""Linux-specific integrations."""

from tunes_player.platform.linux.mpris import MprisService, create_mpris_service

__all__ = ["MprisService", "create_mpris_service"]
