"""Release browse grid layout (pure math, no GTK)."""

from __future__ import annotations

import math

RELEASE_TILE_MIN_EDGE = 140
RELEASE_TILE_MAX_EDGE = 200
RELEASE_GRID_SPACING = 12
RELEASE_GRID_VIEW_MARGIN = 18

def release_grid_min_content_width() -> int:
    """Minimum main-pane width for one column at min tile size (includes grid margins)."""
    return 2 * RELEASE_GRID_VIEW_MARGIN + RELEASE_TILE_MIN_EDGE

def release_grid_inner_width(
    window_width: int,
    *,
    sidebar_width: int,
    horizontal_padding: int,
) -> int:
    """Width available for tile rows, derived from the window."""
    return max(0, window_width - sidebar_width - horizontal_padding)

def release_grid_content_inner_width(
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

def release_grid_resolve_inner_width(
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

def release_grid_layout(inner_width: int) -> tuple[int, int]:
    """Return (columns, tile_edge) for a start-aligned grid that fills inner_width."""
    spacing = RELEASE_GRID_SPACING
    min_edge = RELEASE_TILE_MIN_EDGE
    max_edge = RELEASE_TILE_MAX_EDGE

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

def release_grid_visible_card_indices(
    *,
    card_count: int,
    columns: int,
    tile_edge: int,
    scroll_y: float,
    viewport_height: float,
    margin_top: int = RELEASE_GRID_VIEW_MARGIN,
    prefetch_rows: int = 1,
) -> tuple[int, int]:
    """Return half-open card index range ``[start, end)`` that should load artwork.

    Rows are square tiles of *tile_edge* with ``RELEASE_GRID_SPACING`` between them,
    offset by *margin_top* inside the scroll child.
    """
    if card_count <= 0 or columns <= 0 or tile_edge <= 0 or viewport_height <= 0:
        return (0, 0)

    stride = tile_edge + RELEASE_GRID_SPACING
    scroll_bottom = scroll_y + viewport_height
    num_rows = (card_count + columns - 1) // columns

    first_row = max(0, math.floor((scroll_y - margin_top - tile_edge) / stride) + 1)
    last_row = min(
        num_rows - 1,
        max(0, math.ceil((scroll_bottom - margin_top) / stride) - 1),
    )

    first_row = max(0, first_row - prefetch_rows)
    last_row = min(num_rows - 1, last_row + prefetch_rows)
    if first_row > last_row:
        first_row = max(0, last_row - prefetch_rows)

    start = first_row * columns
    end = min(card_count, (last_row + 1) * columns)
    return (start, end)
