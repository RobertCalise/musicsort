"""Pure-function track categorizer.

`categorize(tags, taxonomy)` returns a `CategorizeResult` with the primary
destination plus secondary matches ranked by specificity. The mover uses
the primary for on-disk routing; the Rekordbox playlists subcommand uses
primary + secondaries for multi-playlist fanout (capped by config).

Specificity rule: the *longest* matching alias wins. A track tagged
"Tech House" routes to Tech House (alias "tech house", 9 chars) rather
than House (alias "house", 5 chars), even when both aliases survive
year-predicate filtering.

Ties on alias length break by earliest token position in the input
string. Still-ambiguous ties → AMBIGUOUS quarantine. Only-manual-only
matches → MANUAL_ONLY quarantine. No matches → UNMATCHED.

Real-world genre tags arrive in several formats the categorizer handles
beyond simple case/whitespace normalization:

- Apple-style slash lists:  "Dance / Pop", "Trap / Hip-Hop / R&B"
- Beatport ampersand pairs: "Bass & Garage", "Melodic House & Techno"
- Comma lists:              "Trance,Trance Progressive,EDM"
- Parenthetical sub-genres: "Trance (Main Floor)"
"""

from __future__ import annotations

import re
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from musicsort.autoimport.reader import TrackTags
from musicsort.autoimport.taxonomy import Category, Taxonomy

_WHITESPACE_RE = re.compile(r"\s+")
_DELIMITER_RE = re.compile(r"[/&,;]")
_PARENS_RE = re.compile(r"\s*\([^)]*\)\s*")


class MatchKind(StrEnum):
    MATCHED = "matched"
    UNMATCHED = "unmatched"
    MANUAL_ONLY = "manual_only"
    AMBIGUOUS = "ambiguous"


class CategorizeResult(BaseModel):
    """Result of categorizing a single track.

    `primary` is the on-disk routing destination; `secondaries` are the
    other surviving matches ranked by descending specificity (longest
    alias next). Both are None / empty when `kind != MATCHED`.
    """

    model_config = ConfigDict(frozen=True)

    kind: MatchKind
    primary: Category | None = None
    secondaries: tuple[Category, ...] = ()
    reason: str


def categorize(tags: TrackTags, taxonomy: Taxonomy) -> CategorizeResult:
    """Map a track's tags to a ranked list of destination categories.

    Returns `CategorizeResult.kind = MATCHED` with `primary` set when at
    least one auto-routing category matches and the top-specificity tier
    isn't ambiguous. Otherwise returns one of UNMATCHED / MANUAL_ONLY /
    AMBIGUOUS with `primary=None`.
    """
    if tags.genre is None or not tags.genre.strip():
        return CategorizeResult(kind=MatchKind.UNMATCHED, reason="no genre tag")

    raw_genre = tags.genre
    matches, year_blocked = _collect_matches(raw_genre, tags.year, taxonomy)
    if not matches:
        if year_blocked:
            return CategorizeResult(
                kind=MatchKind.UNMATCHED,
                reason=(
                    f"genre tag {raw_genre!r} matches year-gated "
                    f"categor{'ies' if len(year_blocked) > 1 else 'y'} "
                    f"{sorted(year_blocked)} but year tag is missing"
                ),
            )
        return CategorizeResult(
            kind=MatchKind.UNMATCHED,
            reason=f"genre tag {raw_genre!r} not in taxonomy",
        )

    # Rank: alias length DESC (most specific first), then candidate-token
    # index ASC (earliest position in the multi-genre string wins ties).
    matches.sort(key=lambda triple: (-triple[1], triple[2]))

    auto = [m for m in matches if not m[0].manual_only]
    if not auto:
        names = [cat.name for cat, _, _ in matches]
        return CategorizeResult(
            kind=MatchKind.MANUAL_ONLY,
            reason=f"category {names} requires manual curation",
        )

    top_length, top_idx = auto[0][1], auto[0][2]
    top_tier = [cat for cat, length, idx in auto if length == top_length and idx == top_idx]
    if len(top_tier) > 1:
        names = [cat.name for cat in top_tier]
        return CategorizeResult(
            kind=MatchKind.AMBIGUOUS,
            reason=(
                f"genre tag {raw_genre!r} matched multiple categories with "
                f"same specificity: {names}"
            ),
        )

    primary = auto[0][0]
    secondaries = tuple(cat for cat, _, _ in auto[1:])
    return CategorizeResult(
        kind=MatchKind.MATCHED,
        primary=primary,
        secondaries=secondaries,
        reason=f"genre tag {raw_genre!r} matched {primary.name}",
    )


def _collect_matches(
    raw_genre: str,
    year: int | None,
    taxonomy: Taxonomy,
) -> tuple[list[tuple[Category, int, int]], set[str]]:
    """Collect (category, alias_length, candidate_idx) triples across tokens.

    For each category, find the best-matching alias from any candidate
    token. "Best" means: longest alias; ties broken by earliest candidate
    index. Categories whose `when:` clause fails the year predicate are
    dropped.

    Returns (matched-triples, year_blocked_category_names). The second
    set is non-empty when an alias DID match a year-gated category but
    the track has no year tag — used to give the caller a precise
    "year required" UNMATCHED reason instead of a generic one.
    """
    candidates = _genre_candidates(raw_genre)
    normalized_candidates = [_normalize(c) for c in candidates]
    results: dict[str, tuple[Category, int, int]] = {}
    year_blocked: set[str] = set()
    for cat in taxonomy.categories:
        cat_matches_some_alias = any(
            _normalize(alias) == nc for alias in cat.aliases for nc in normalized_candidates
        )
        if not _when_satisfied(cat, year):
            if cat_matches_some_alias and year is None and cat.when is not None:
                year_blocked.add(cat.name)
            continue
        if not cat_matches_some_alias:
            continue
        best_length = -1
        best_idx = len(candidates)
        for idx, nc in enumerate(normalized_candidates):
            for alias in cat.aliases:
                normalized_alias = _normalize(alias)
                if normalized_alias != nc:
                    continue
                alen = len(normalized_alias)
                if alen > best_length or (alen == best_length and idx < best_idx):
                    best_length = alen
                    best_idx = idx
        if best_length >= 0:
            results[cat.name] = (cat, best_length, best_idx)
    return list(results.values()), year_blocked


def _normalize(value: str) -> str:
    return _WHITESPACE_RE.sub(" ", value.strip().lower())


def _genre_candidates(raw_genre: str) -> list[str]:
    """Yield genre tokens to try, in priority order.

    Order:
      1. The original tag (handles 'House', 'Tech House', 'Pop Punk' etc.)
      2. The tag with parenthetical sub-genres stripped
      3. Each token split on `/`, `&`, `,`, `;`

    Deduplicated; empty tokens dropped.
    """
    seen: set[str] = set()
    candidates: list[str] = []

    def _add(value: str) -> None:
        v = value.strip()
        if v and v not in seen:
            seen.add(v)
            candidates.append(v)

    _add(raw_genre)
    stripped = _PARENS_RE.sub(" ", raw_genre).strip()
    _add(stripped)
    for token in _DELIMITER_RE.split(stripped):
        _add(token)
    return candidates


def _when_satisfied(category: Category, year: int | None) -> bool:
    """A category with a `when:` clause whose predicates need a year is dropped
    when `year` is None. A category with no `when:` is always satisfied."""
    when = category.when
    if when is None:
        return True
    if when.year_lt is not None and (year is None or year >= when.year_lt):
        return False
    return not (when.year_gte is not None and (year is None or year < when.year_gte))
