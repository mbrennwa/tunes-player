"""Label filter: menu button with checkmark list in a popover."""

from __future__ import annotations

from collections.abc import Callable

from tunes_player.ui.gtk.searchable_check_filter import SearchableCheckFilterMenu

EnabledLabelsChanged = Callable[[frozenset[str]], None]


class LabelFilterMenu(SearchableCheckFilterMenu):
    """Heading plus menu button; popover holds searchable checkmark list."""

    def __init__(self, *, on_changed: EnabledLabelsChanged | None = None) -> None:
        super().__init__(
            heading="Label",
            all_selected_label="All labels",
            search_placeholder="Filter labels…",
            on_changed=on_changed,
        )

    def set_labels(
        self,
        labels: tuple[str, ...],
        enabled_labels: frozenset[str],
    ) -> None:
        self.set_items(labels, enabled_labels)

    def set_enabled_labels(self, enabled_labels: frozenset[str]) -> None:
        self.set_enabled(enabled_labels)
