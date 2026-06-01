"""Album browse grid layout (pure math, no GTK)."""

from __future__ import annotations

ALBUM_TILE_MIN_EDGE = 140
ALBUM_TILE_MAX_EDGE = 200
ALBUM_GRID_SPACING = 12
ALBUM_GRID_VIEW_MARGIN = 18
SEARCH_VIEW_HORIZONTAL_MARGIN = 12


def album_grid_min_content_width() -> int:
    """Minimum main-pane width for one column at min tile size (includes grid margins)."""
    return 2 * ALBUM_GRID_VIEW_MARGIN + ALBUM_TILE_MIN_EDGE


def search_grid_min_content_width() -> int:
    """Minimum width for search album tiles (search box margins + min tile)."""
    return 2 * SEARCH_VIEW_HORIZONTAL_MARGIN + ALBUM_TILE_MIN_EDGE


def album_grid_inner_width(
    window_width: int,
    *,
    sidebar_width: int,
    horizontal_padding: int,
) -> int:
    """Width available for tile rows, derived from the window."""
    return max(0, window_width - sidebar_width - horizontal_padding)


def album_grid_content_inner_width(
    allocated_width: int,
    *,
    margin_start: int = 0,
    margin_end: int = 0,
) -> int:
    """Width for tile rows from the grid widget's own allocation (not the window)."""
    if allocated_width < 1:
        return 0
    return max(0, allocated_width - margin_start - margin_end)


def album_grid_layout(inner_width: int) -> tuple[int, int]:
    """Return (columns, tile_edge) for a start-aligned grid that fills inner_width."""
    spacing = ALBUM_GRID_SPACING
    min_edge = ALBUM_TILE_MIN_EDGE
    max_edge = ALBUM_TILE_MAX_EDGE

    if inner_width < min_edge:
        return 1, min_edge

    slot_max = max_edge + spacing
    columns = max(1, (inner_width + spacing + max_edge - 1) // slot_max)
    edge = (inner_width - spacing * (columns - 1)) // columns

    if edge < min_edge:
        columns = max(1, (inner_width + spacing) // (min_edge + spacing))
        edge = (inner_width - spacing * (columns - 1)) // columns

    edge = max(min_edge, min(edge, max_edge))
    return columns, edge
