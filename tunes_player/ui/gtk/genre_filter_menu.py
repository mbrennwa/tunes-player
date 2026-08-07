"""Genre filter: menu button with checkmark list in a popover."""

from __future__ import annotations

from collections.abc import Callable

from tunes_player.ui.gtk.searchable_check_filter import (
    SearchableCheckFilterMenu,
    genre_filter_list_max_height,
)

EnabledGenresChanged = Callable[[frozenset[str]], None]

__all__ = [
    "EnabledGenresChanged",
    "GenreFilterMenu",
    "genre_filter_list_max_height",
]


class GenreFilterMenu(SearchableCheckFilterMenu):
    """Heading plus menu button; popover holds searchable checkmark list."""

    def __init__(self, *, on_changed: EnabledGenresChanged | None = None) -> None:
        super().__init__(
            heading="Genre",
            all_selected_label="All genres",
            search_placeholder="Filter genres…",
            on_changed=on_changed,
        )

    def set_genres(
        self,
        genres: tuple[str, ...],
        enabled_genres: frozenset[str],
    ) -> None:
        self.set_items(genres, enabled_genres)

    def set_enabled_genres(self, enabled_genres: frozenset[str]) -> None:
        self.set_enabled(enabled_genres)

    def get_enabled_genres(self) -> frozenset[str]:
        return self.get_enabled()
