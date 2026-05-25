"""Tests for the pure-function categorizer.

Covers the v2 taxonomy: most-specific-alias-wins, ranked primary +
secondaries, year-gated splits, manual_only quarantine, ambiguous-tie
quarantine.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from musicsort.autoimport.categorizer import (
    CategorizeResult,
    MatchKind,
    categorize,
)
from musicsort.autoimport.reader import TrackTags
from musicsort.autoimport.taxonomy import load_taxonomy
from musicsort.config import get_settings


@pytest.fixture(scope="module")
def taxonomy():
    return load_taxonomy(get_settings().taxonomy_path)


EXACT_MATCHES: list[tuple[str, int | None, str]] = [
    ("House", 2010, "House"),
    ("Deep House", 2010, "House"),
    ("Tech House", None, "Tech_House"),
    ("Progressive House", None, "Progressive_House"),
    ("Future House", None, "Future_House"),
    ("Melodic House", None, "Melodic_House_Techno"),
    ("Afro House", None, "Afro_House"),
    ("Bass House", None, "Bass_House"),
    ("Nu Disco", None, "Disco_NuDisco"),
    ("Techno", None, "Techno_PeakTime"),
    ("Hard Techno", None, "Techno_Hard"),
    ("Deep Techno", None, "Techno_RawDeep"),
    ("Trance", None, "Trance_MainFloor"),
    ("Psy Trance", None, "Psy_Trance"),
    ("Dubstep", None, "Dubstep"),
    ("Drum & Bass", None, "DnB"),
    ("DnB", None, "DnB"),
    ("UK Garage", None, "UK_Garage_Bassline"),
    ("2-Step", None, "UK_Garage_Bassline"),
    ("Hardstyle", None, "Hard_Dance"),
    ("Mainstage", None, "Mainstage"),
    ("Big Room", None, "Mainstage"),
    ("EDM", None, "Mainstage"),
    ("Downtempo", None, "Downtempo"),
    ("Ambient", None, "Ambient_Experimental"),
    ("Eurodance", None, "Eurodance"),
    ("Europop", None, "Europop"),
    ("Amapiano", None, "Amapiano"),
    ("Afrobeats", None, "Afrobeats"),
    ("Hip-Hop", None, "Hip_Hop"),
    ("Rap", None, "Hip_Hop"),
    ("Trap", None, "Hip_Hop_Trap"),
    ("R&B", None, "R_and_B"),
    ("Soul", None, "R_and_B"),
    ("Funk", None, "Funk"),
    ("Rock", None, "Rock"),
    ("Classic Rock", None, "Rock"),
    ("Alternative Rock", None, "Alternative_Rock"),
    ("Indie Rock", None, "Alternative_Rock"),
    ("Soft Rock", None, "Soft_Rock"),
    ("Piano Rock", None, "Piano_Rock"),
    ("Metal", None, "Metal"),
    ("Heavy Metal", None, "Metal"),
    ("Punk", None, "Punk"),
    ("Pop Punk", None, "Pop_Punk"),
    ("Blues", None, "Blues"),
    ("Reggae", None, "Reggae"),
    ("Dancehall", None, "Reggae"),
    ("Lo-Fi", None, "Lo_Fi"),
    ("LoFi", None, "Lo_Fi"),
    ("Country", None, "Country"),
    ("Jazz", None, "Jazz"),
    ("Classical", None, "Classical"),
    ("Folk", None, "Folk"),
    ("Acoustic", None, "Acoustic"),
    ("Christian", None, "Christian_Gospel"),
    ("Gospel", None, "Christian_Gospel"),
    ("Devotional", None, "Christian_Gospel"),
    ("Soundtrack", None, "Soundtrack"),
    ("Latin", None, "Latin"),
    ("World", None, "World"),
]


@pytest.mark.parametrize(("genre", "year", "folder"), EXACT_MATCHES)
def test_exact_alias_match(genre: str, year: int | None, folder: str, taxonomy) -> None:
    result = categorize(TrackTags(genre=genre, year=year), taxonomy)
    assert result.kind is MatchKind.MATCHED, f"{genre!r} -> {result.kind} ({result.reason})"
    assert result.primary is not None
    assert result.primary.folder == folder


def test_case_insensitive(taxonomy) -> None:
    result = categorize(TrackTags(genre="  TECH HOUSE  "), taxonomy)
    assert result.kind is MatchKind.MATCHED
    assert result.primary is not None
    assert result.primary.folder == "Tech_House"


def test_internal_whitespace_collapsed(taxonomy) -> None:
    result = categorize(TrackTags(genre="Tech   House"), taxonomy)
    assert result.kind is MatchKind.MATCHED
    assert result.primary is not None
    assert result.primary.folder == "Tech_House"


def test_tech_house_beats_house(taxonomy) -> None:
    """The 'tech house' alias (9 chars) wins over the 'house' alias (5)."""
    result = categorize(TrackTags(genre="Tech House"), taxonomy)
    assert result.kind is MatchKind.MATCHED
    assert result.primary is not None
    assert result.primary.name == "Tech House"


def test_secondaries_ranked_by_specificity(taxonomy) -> None:
    """Multi-genre slash list returns primary + ranked secondaries."""
    result = categorize(TrackTags(genre="Tech House / Deep House", year=2010), taxonomy)
    assert result.kind is MatchKind.MATCHED
    assert result.primary is not None
    assert result.primary.name == "Tech House"
    secondary_names = [c.name for c in result.secondaries]
    assert "House" in secondary_names


def test_same_category_via_two_aliases_dedupes(taxonomy) -> None:
    result = categorize(TrackTags(genre="House / Deep House", year=2010), taxonomy)
    assert result.kind is MatchKind.MATCHED
    assert result.primary is not None
    assert result.primary.name == "House"
    house_count = sum(1 for c in [result.primary, *result.secondaries] if c.name == "House")
    assert house_count == 1


@pytest.mark.parametrize(
    ("year", "expected_folder"),
    [
        (1985, "Pop_80s_90s"),
        (1999, "Pop_80s_90s"),
        (2000, "Pop_Modern"),
        (2005, "Pop_Modern"),
        (2024, "Pop_Modern"),
    ],
)
def test_pop_year_split(year: int, expected_folder: str, taxonomy) -> None:
    result = categorize(TrackTags(genre="Pop", year=year), taxonomy)
    assert result.kind is MatchKind.MATCHED
    assert result.primary is not None
    assert result.primary.folder == expected_folder


def test_pop_without_year_unmatched(taxonomy) -> None:
    result = categorize(TrackTags(genre="Pop"), taxonomy)
    assert result.kind is MatchKind.UNMATCHED
    assert result.primary is None


@pytest.mark.parametrize(
    ("year", "expected_folder"),
    [
        (1985, "House_Classics"),
        (1999, "House_Classics"),
        (2000, "House"),
        (2010, "House"),
    ],
)
def test_house_classics_year_split(year: int, expected_folder: str, taxonomy) -> None:
    result = categorize(TrackTags(genre="Deep House", year=year), taxonomy)
    assert result.kind is MatchKind.MATCHED
    assert result.primary is not None
    assert result.primary.folder == expected_folder


def test_house_without_year_quarantines(taxonomy) -> None:
    result = categorize(TrackTags(genre="House"), taxonomy)
    assert result.kind is MatchKind.UNMATCHED
    assert result.primary is None


def test_acapella_is_manual_only(taxonomy) -> None:
    result = categorize(TrackTags(genre="Acapella"), taxonomy)
    assert result.kind is MatchKind.MANUAL_ONLY
    assert "Acapellas" in result.reason
    assert result.primary is None


def test_dj_tools_is_manual_only(taxonomy) -> None:
    result = categorize(TrackTags(genre="DJ Tools"), taxonomy)
    assert result.kind is MatchKind.MANUAL_ONLY
    assert "DJ Tools" in result.reason


def test_podcasts_route_to_non_music(taxonomy) -> None:
    result = categorize(TrackTags(genre="Podcasts"), taxonomy)
    assert result.kind is MatchKind.MANUAL_ONLY
    assert "Non-Music" in result.reason


def test_unknown_genre_unmatched(taxonomy) -> None:
    result = categorize(TrackTags(genre="ZZZ_NotAGenre"), taxonomy)
    assert result.kind is MatchKind.UNMATCHED
    assert "ZZZ_NotAGenre" in result.reason


@pytest.mark.parametrize("genre", [None, "", "   ", "\t\n "])
def test_missing_genre_unmatched(genre: str | None, taxonomy) -> None:
    result = categorize(TrackTags(genre=genre), taxonomy)
    assert result.kind is MatchKind.UNMATCHED
    assert result.reason == "no genre tag"


def test_result_is_frozen() -> None:
    result = CategorizeResult(kind=MatchKind.UNMATCHED, reason="x")
    with pytest.raises(ValidationError):
        result.reason = "y"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("genre", "expected_folder"),
    [
        ("Trance (Main Floor)", "Trance_MainFloor"),
        ("Techno (Peak Time / Driving)", "Techno_PeakTime"),
        ("Techno (Raw / Deep / Hypnotic)", "Techno_RawDeep"),
        ("Nu Disco / Disco", "Disco_NuDisco"),
        ("Bass / Club", "Bass_Club"),
        ("Melodic House & Techno", "Melodic_House_Techno"),
        ("Dance / Pop", "Europop"),
    ],
)
def test_compound_tags_route_correctly(taxonomy, genre: str, expected_folder: str) -> None:
    result = categorize(TrackTags(genre=genre), taxonomy)
    assert result.kind is MatchKind.MATCHED, f"{genre!r} -> {result.kind} ({result.reason})"
    assert result.primary is not None
    assert result.primary.folder == expected_folder
