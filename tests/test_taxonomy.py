"""Tests for the taxonomy schema and multi-file loader."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from musicsort.autoimport.taxonomy import Category, Taxonomy, load_taxonomy
from musicsort.config import get_settings

# Spot-check that key categories are present in the bundled taxonomy.
EXPECTED_FOLDERS: frozenset[str] = frozenset(
    {
        "House",
        "House_Classics",
        "Tech_House",
        "Techno_PeakTime",
        "Trance_MainFloor",
        "DnB",
        "Europop",
        "Pop_Modern",
        "Pop_80s_90s",
        "Hip_Hop",
        "Rock",
        "Metal",
        "Punk",
        "Funk",
        "Acoustic",
        "Christian_Gospel",
        "Acapellas",
        "Non_Music",
    }
)


def test_bundled_taxonomy_loads() -> None:
    taxonomy = load_taxonomy(get_settings().taxonomy_path)
    assert taxonomy.version == 2
    assert len(taxonomy.categories) >= 60


def test_bundled_taxonomy_has_expected_folders() -> None:
    taxonomy = load_taxonomy(get_settings().taxonomy_path)
    folders = {cat.folder for cat in taxonomy.categories}
    missing = EXPECTED_FOLDERS - folders
    assert not missing, f"missing expected folders: {missing}"


def test_bundled_taxonomy_populates_aliases() -> None:
    """The loader merges all genres/*.yaml mappings into per-category aliases."""
    taxonomy = load_taxonomy(get_settings().taxonomy_path)
    house = next(c for c in taxonomy.categories if c.name == "House")
    assert "house" in house.aliases or "House" in house.aliases
    tech_house = next(c for c in taxonomy.categories if c.name == "Tech House")
    assert any(a.lower() == "tech house" for a in tech_house.aliases)


def test_house_classics_mirrors_house_aliases() -> None:
    """List-form mapping puts shared aliases on both year-gated buckets."""
    taxonomy = load_taxonomy(get_settings().taxonomy_path)
    house = next(c for c in taxonomy.categories if c.name == "House")
    house_classics = next(c for c in taxonomy.categories if c.name == "House Classics")
    house_aliases = {a.lower() for a in house.aliases}
    classics_aliases = {a.lower() for a in house_classics.aliases}
    shared = house_aliases & classics_aliases
    assert "house" in shared
    assert "deep house" in shared


def test_family_field_loads() -> None:
    taxonomy = load_taxonomy(get_settings().taxonomy_path)
    tech_house = next(c for c in taxonomy.categories if c.name == "Tech House")
    assert tech_house.family == "House"
    pop = next(c for c in taxonomy.categories if c.name == "Pop")
    assert pop.family is None


def test_duplicate_folder_rejected(tmp_path: Path) -> None:
    bad = tmp_path / "genres.yaml"
    bad.write_text(
        """
version: 2
categories:
  - name: A
    folder: Same
  - name: B
    folder: Same
""",
        encoding="utf-8",
    )
    with pytest.raises(ValidationError, match="duplicate folder"):
        load_taxonomy(bad)


def test_duplicate_name_rejected(tmp_path: Path) -> None:
    bad = tmp_path / "genres.yaml"
    bad.write_text(
        """
version: 2
categories:
  - name: Same
    folder: A
  - name: Same
    folder: B
""",
        encoding="utf-8",
    )
    with pytest.raises(ValidationError, match="duplicate category name"):
        load_taxonomy(bad)


def test_unknown_alias_target_rejected(tmp_path: Path) -> None:
    bad = tmp_path / "genres.yaml"
    bad.write_text(
        """
version: 2
categories:
  - name: Real
    folder: Real
""",
        encoding="utf-8",
    )
    mappings_dir = tmp_path / "genres"
    mappings_dir.mkdir()
    (mappings_dir / "x.yaml").write_text(
        """
version: 1
source: Test
mappings:
  alias: NonexistentCategory
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="unknown category"):
        load_taxonomy(bad)


def test_list_form_alias_targets_multiple_categories(tmp_path: Path) -> None:
    """A mapping value list spreads the alias to every listed category."""
    bad = tmp_path / "genres.yaml"
    bad.write_text(
        """
version: 2
categories:
  - name: A
    folder: A
  - name: B
    folder: B
""",
        encoding="utf-8",
    )
    mappings_dir = tmp_path / "genres"
    mappings_dir.mkdir()
    (mappings_dir / "x.yaml").write_text(
        """
version: 1
source: Test
mappings:
  shared:
    - A
    - B
""",
        encoding="utf-8",
    )
    taxonomy = load_taxonomy(bad)
    by_name = {c.name: c for c in taxonomy.categories}
    assert "shared" in by_name["A"].aliases
    assert "shared" in by_name["B"].aliases


def test_minimal_taxonomy_constructs() -> None:
    t = Taxonomy(version=2, categories=())
    assert t.version == 2
    assert t.categories == ()


def test_category_extra_field_rejected() -> None:
    with pytest.raises(ValidationError):
        Category(name="X", folder="X", color="blue")  # type: ignore[call-arg]
