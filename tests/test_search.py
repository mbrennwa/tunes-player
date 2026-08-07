"""Search query parsing and library AND-match behavior (#79)."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from tunes_player.core.library import ids
from tunes_player.core.library.db import connect
from tunes_player.core.library.store import LibraryStore
from tunes_player.core.models import Release, Source
from tunes_player.core.search_query import (
    SearchTerm,
    parse_search_query,
    release_matches_query,
    text_matches_terms,
)


def _release(*, title: str, artist: str, release_id: str = "local:test") -> Release:
    return Release(
        id=release_id,
        title=title,
        artist_name=artist,
        source=Source.LOCAL,
    )


def _insert_track(
    connection,
    *,
    path: str,
    album: str,
    album_artist: str,
    title: str,
    artist: str | None = None,
) -> str:
    album_id = ids.release_id(album_artist, album)
    file_id = connection.execute(
        "INSERT INTO files(path, mtime_ns, size_bytes, indexed_at_ns) VALUES (?, ?, ?, ?)",
        (path, 1, 1, 1),
    ).lastrowid
    connection.execute(
        """
        INSERT INTO tracks(
            id, file_id, album_id, title, artist, album_artist, album, is_synthetic
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 0)
        """,
        (
            f"local:file:{path}",
            file_id,
            album_id,
            title,
            artist if artist is not None else album_artist,
            album_artist,
            album,
        ),
    )
    return album_id


class TestParseSearchQuery(unittest.TestCase):
    def test_empty(self) -> None:
        self.assertEqual(parse_search_query("  ").terms, ())

    def test_single_token(self) -> None:
        parsed = parse_search_query("pink")
        self.assertEqual(parsed.terms, (SearchTerm(text="pink", phrase=False),))
        self.assertEqual(parsed.plain_query, "pink")

    def test_and_tokens(self) -> None:
        parsed = parse_search_query("  miles   davis ")
        self.assertEqual(
            parsed.terms,
            (
                SearchTerm(text="miles", phrase=False),
                SearchTerm(text="davis", phrase=False),
            ),
        )
        self.assertEqual(parsed.plain_query, "miles davis")

    def test_phrase(self) -> None:
        parsed = parse_search_query('"kind of blue"')
        self.assertEqual(
            parsed.terms,
            (SearchTerm(text="kind of blue", phrase=True),),
        )
        self.assertEqual(parsed.plain_query, "kind of blue")

    def test_mixed_phrase_and_tokens(self) -> None:
        parsed = parse_search_query('miles "kind of blue" davis')
        self.assertEqual(
            parsed.terms,
            (
                SearchTerm(text="miles", phrase=False),
                SearchTerm(text="kind of blue", phrase=True),
                SearchTerm(text="davis", phrase=False),
            ),
        )

    def test_unbalanced_quote_to_end(self) -> None:
        parsed = parse_search_query('hello "open phrase')
        self.assertEqual(
            parsed.terms,
            (
                SearchTerm(text="hello", phrase=False),
                SearchTerm(text="open phrase", phrase=True),
            ),
        )

    def test_empty_phrase_skipped(self) -> None:
        parsed = parse_search_query('a "" b')
        self.assertEqual(
            parsed.terms,
            (
                SearchTerm(text="a", phrase=False),
                SearchTerm(text="b", phrase=False),
            ),
        )


class TestTextMatchesTerms(unittest.TestCase):
    def test_and_casefold(self) -> None:
        terms = (
            SearchTerm(text="Miles", phrase=False),
            SearchTerm(text="davis", phrase=False),
        )
        self.assertTrue(text_matches_terms("The Miles Davis Quintet", terms))
        self.assertFalse(text_matches_terms("Miles Alone", terms))

    def test_empty_terms_do_not_match(self) -> None:
        self.assertFalse(text_matches_terms("anything", ()))

    def test_word_order_flexible(self) -> None:
        terms = (
            SearchTerm(text="davis", phrase=False),
            SearchTerm(text="miles", phrase=False),
        )
        self.assertTrue(text_matches_terms("Miles Davis", terms))


class TestReleaseMatchesQuery(unittest.TestCase):
    def test_title_and_artist_haystack(self) -> None:
        release = _release(title="Kind of Blue", artist="Miles Davis")
        parsed = parse_search_query("kind miles")
        self.assertTrue(release_matches_query(release, parsed))
        self.assertFalse(release_matches_query(release, parse_search_query("coltrane")))

    def test_artists_only(self) -> None:
        release = _release(title="Kind of Blue", artist="Miles Davis")
        parsed = parse_search_query("kind")
        self.assertTrue(release_matches_query(release, parsed))
        self.assertFalse(
            release_matches_query(release, parsed, artists_only=True),
        )
        self.assertTrue(
            release_matches_query(
                release, parse_search_query("miles davis"), artists_only=True
            ),
        )

    def test_phrase_on_title(self) -> None:
        release = _release(title="Kind of Blue", artist="Miles Davis")
        self.assertTrue(
            release_matches_query(release, parse_search_query('"kind of blue"')),
        )
        self.assertFalse(
            release_matches_query(release, parse_search_query('"blue kind"')),
        )


class TestLibraryStoreSearchAnd(unittest.TestCase):
    def _store_with_releases(self, tmp: str) -> tuple[LibraryStore, dict[str, str]]:
        db_path = Path(tmp) / "library.db"
        store = LibraryStore(db_path)
        connection = connect(db_path)
        try:
            miles = _insert_track(
                connection,
                path="/music/miles.flac",
                album="Kind of Blue",
                album_artist="Miles Davis",
                title="So What",
            )
            floyd = _insert_track(
                connection,
                path="/music/floyd.flac",
                album="The Wall",
                album_artist="Pink Floyd",
                title="Hey You",
            )
            pink_moon = _insert_track(
                connection,
                path="/music/drake.flac",
                album="Pink Moon",
                album_artist="Nick Drake",
                title="Pink Moon",
            )
            connection.commit()
        finally:
            connection.close()
        return store, {"miles": miles, "floyd": floyd, "pink_moon": pink_moon}

    def test_single_token(self) -> None:
        with TemporaryDirectory() as tmp:
            store, ids_map = self._store_with_releases(tmp)
            results = store.search_releases("pink")
            result_ids = {r.id for r in results}
            self.assertIn(ids_map["floyd"], result_ids)
            self.assertIn(ids_map["pink_moon"], result_ids)
            self.assertNotIn(ids_map["miles"], result_ids)

    def test_and_tokens_narrow(self) -> None:
        with TemporaryDirectory() as tmp:
            store, ids_map = self._store_with_releases(tmp)
            results = store.search_releases("pink floyd")
            result_ids = {r.id for r in results}
            self.assertEqual(result_ids, {ids_map["floyd"]})

    def test_phrase_match(self) -> None:
        with TemporaryDirectory() as tmp:
            store, ids_map = self._store_with_releases(tmp)
            results = store.search_releases('"kind of blue"')
            result_ids = {r.id for r in results}
            self.assertEqual(result_ids, {ids_map["miles"]})

    def test_word_order_flexible(self) -> None:
        with TemporaryDirectory() as tmp:
            store, ids_map = self._store_with_releases(tmp)
            results = store.search_releases("davis miles")
            result_ids = {r.id for r in results}
            self.assertEqual(result_ids, {ids_map["miles"]})

    def test_artists_only(self) -> None:
        with TemporaryDirectory() as tmp:
            store, ids_map = self._store_with_releases(tmp)
            # "blue" is in the album title, not album_artist.
            results = store.search_releases("blue", artists_only=True)
            self.assertEqual(results, [])
            results = store.search_releases("miles davis", artists_only=True)
            self.assertEqual({r.id for r in results}, {ids_map["miles"]})

    def test_empty_query(self) -> None:
        with TemporaryDirectory() as tmp:
            store, _ = self._store_with_releases(tmp)
            self.assertEqual(store.search_releases("   "), [])


if __name__ == "__main__":
    unittest.main()
