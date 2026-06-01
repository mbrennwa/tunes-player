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
    outer_width: int,
    *,
    margin_start: int = 0,
    margin_end: int = 0,
) -> int:
    """Tile row width from a parent box width (subtract margins once).

    GTK 4 ``Widget.get_width()`` on a margined child is already the content
    width inside those margins — do not pass that value through this helper.
    """
    if outer_width < 1:
        return 0
    return max(0, outer_width - margin_start - margin_end)


def album_grid_resolve_inner_width(
    *,
    viewport_inner: int,
    window_inner: int,
    last_viewport_inner: int = 0,
    last_window_inner: int = 0,
) -> tuple[int, int, int]:
    """Choose tile row width for relayout.

    Viewport width (GtkScrolledWindow) tracks shrink while the window can lag
    behind wide tiles. Window width tracks grow while the scroll viewport can
    lag behind a narrow child. Use direction of change to pick the leading edge.
    Returns ``(inner_width, last_viewport_inner, last_window_inner)``.
    """
    vp = max(0, viewport_inner)
    win = max(0, window_inner)
    if vp < 1 and win < 1:
        return 0, vp, win
    if vp < 1:
        return win, vp, win
    if win < 1:
        return vp, vp, win

    growing = win > last_window_inner + 2
    shrinking = vp < last_viewport_inner - 2
    if growing:
        inner = win
    elif shrinking:
        inner = vp
    elif vp < win - 4:
        inner = vp
    else:
        inner = win
    return inner, vp, win


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
