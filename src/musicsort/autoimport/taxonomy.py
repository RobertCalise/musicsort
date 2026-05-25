"""Taxonomy data model and multi-file YAML loader.

The taxonomy spec is split across two layers:

  - `genres.yaml` carries category *definitions* (display name, folder name,
    optional family for Rekordbox sidebar grouping, year-gating predicates,
    manual_only flag). No aliases live here.

  - `genres/*.yaml` mapping files carry tag-to-category aliases, one per
    source (generic, beatport, apple, bandcamp, ...). The loader unions
    them at load time and attaches the merged alias set to each Category.

The split exists so adding support for a new tag source (e.g. Discogs)
is a single new file in `genres/`. See `docs/taxonomy.md`.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

_NORMALIZE_RE = re.compile(r"\s+")


class WhenClause(BaseModel):
    """Optional per-category gating predicate against tag fields beyond genre."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    year_lt: int | None = None
    year_gte: int | None = None


class Category(BaseModel):
    """One routing destination.

    `family` groups categories into a Rekordbox folder (e.g. all House
    subgenres → `Genres/House/`). Categories without a family land at
    `Genres/` directly. Folder is the filesystem-safe on-disk subdir;
    `name` is the human-readable Rekordbox playlist display.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    folder: str
    family: str | None = None
    aliases: tuple[str, ...] = Field(default_factory=tuple)
    when: WhenClause | None = None
    manual_only: bool = False


class Taxonomy(BaseModel):
    """The full taxonomy. Categories carry routing rules; matching is in `categorizer`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: int
    categories: tuple[Category, ...]

    @model_validator(mode="after")
    def _folders_must_be_unique(self) -> Taxonomy:
        seen: set[str] = set()
        for cat in self.categories:
            if cat.folder in seen:
                raise ValueError(f"duplicate folder: {cat.folder!r}")
            seen.add(cat.folder)
        return self

    @model_validator(mode="after")
    def _names_must_be_unique(self) -> Taxonomy:
        seen: set[str] = set()
        for cat in self.categories:
            if cat.name in seen:
                raise ValueError(f"duplicate category name: {cat.name!r}")
            seen.add(cat.name)
        return self


def load_taxonomy(genres_yaml: Path) -> Taxonomy:
    """Load `genres.yaml` plus all sibling `genres/*.yaml` mapping files.

    `genres_yaml` is the path to the categories definition. The loader
    looks for mapping files in `genres_yaml.parent / "genres" / *.yaml`
    and merges them into per-category alias tuples.
    """
    with genres_yaml.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}

    raw_categories: list[dict] = list(raw.get("categories") or [])
    version = int(raw.get("version", 1))
    categories_by_name: dict[str, dict] = {c["name"]: c for c in raw_categories}

    aliases_by_category: dict[str, list[str]] = {name: [] for name in categories_by_name}

    mappings_dir = genres_yaml.parent / "genres"
    if mappings_dir.is_dir():
        for mapping_file in sorted(mappings_dir.glob("*.yaml")):
            _load_mapping_file(mapping_file, categories_by_name, aliases_by_category)

    final_categories: list[Category] = []
    for raw_cat in raw_categories:
        name = raw_cat["name"]
        when_data = raw_cat.get("when")
        cat = Category(
            name=name,
            folder=raw_cat["folder"],
            family=raw_cat.get("family"),
            aliases=tuple(aliases_by_category[name]),
            when=WhenClause(**when_data) if when_data else None,
            manual_only=bool(raw_cat.get("manual_only", False)),
        )
        final_categories.append(cat)

    return Taxonomy(version=version, categories=tuple(final_categories))


def _load_mapping_file(
    path: Path,
    categories_by_name: dict[str, dict],
    aliases_by_category: dict[str, list[str]],
) -> None:
    """Merge one `genres/<source>.yaml` mapping file into the alias accumulator.

    Mapping value can be:
      - a string: the alias routes to that one category.
      - a list of strings: the alias routes to multiple categories. Used
        for year-gated splits where Pop and Pop (80s/90s) both need to
        share aliases (the categorizer's `when:` predicates disambiguate).
    """
    with path.open("r", encoding="utf-8") as fh:
        doc = yaml.safe_load(fh) or {}
    file_mappings = doc.get("mappings") or {}

    for alias, raw_dest in file_mappings.items():
        alias_str = str(alias)
        dests: list[str] = (
            [str(d) for d in raw_dest] if isinstance(raw_dest, list) else [str(raw_dest)]
        )
        normalized = _normalize_alias(alias_str)
        for dest in dests:
            if dest not in categories_by_name:
                raise ValueError(
                    f"{path.name}: alias {alias_str!r} points to unknown "
                    f"category {dest!r} (not defined in genres.yaml)"
                )
            existing = {_normalize_alias(a) for a in aliases_by_category[dest]}
            if normalized not in existing:
                aliases_by_category[dest].append(alias_str)


def _normalize_alias(alias: str) -> str:
    """Whitespace-collapse + lower-case. Matches the categorizer's normalization."""
    return _NORMALIZE_RE.sub(" ", alias.strip().lower())
