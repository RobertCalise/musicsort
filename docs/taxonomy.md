# musicsort taxonomy

How musicsort decides where to put an audio file. This document explains
the *why*; the machine-readable spec lives in
[`src/musicsort/autoimport/genres.yaml`](../src/musicsort/autoimport/genres.yaml)
and the per-source mapping files at
[`src/musicsort/autoimport/genres/`](../src/musicsort/autoimport/genres/).

If a tag your store writes isn't routing correctly, the fix is usually a
one-line addition to a `genres/*.yaml` file — **open an issue or PR**.

---

## The contract

**Input:** an audio file with a `Genre` ID3 tag (and optionally a `Year`
tag).

**Outputs:**

1. **One on-disk destination**: `Library/Songs/<Folder>/<file>` — the
   *primary* category, picked by the matching rule below.
2. **Zero-or-more Rekordbox genre playlists**: when you run
   `musicsort rekordbox playlists --genres`, the track is added to up to
   `rekordbox_playlist_fanout` (default 3) playlists, ranked by alias
   specificity. Same-category duplicates are de-duped.
3. **One decade playlist** (optional): when you run
   `musicsort rekordbox playlists --decades`, files with a `Year` tag
   also land in `Decades/<NNs>` (`80s`, `90s`, `2000s`, `2010s`, …).

Tracks with no `Genre` tag, or a `Genre` that doesn't match any alias,
land in `Library/_Unsorted/no_genre/` or `Library/_Unsorted/unknown_genre/`
for manual review.

---

## File structure

```
src/musicsort/autoimport/
├── genres.yaml          ← THE category list. Source of truth.
└── genres/              ← Per-source tag-to-category mappings.
    ├── generic.yaml     ← Canonical aliases used by anyone.
    ├── beatport.yaml    ← Beatport's literal compound bucket names.
    ├── apple.yaml       ← Apple Music subgenres that don't fit generic.
    └── bandcamp.yaml    ← Bandcamp's flat 28-tag list.
```

**`genres.yaml`** holds category *definitions*: display name, on-disk
folder name, optional family (for Rekordbox sidebar grouping), year-gating
predicates, and a `manual_only` flag for specials. No aliases live here.

**`genres/*.yaml`** are tag-to-category-name maps. The categorizer
unions them at load time; every alias must point to a category defined
in `genres.yaml`.

Adding support for a new tag source (e.g. Discogs, Traxsource) is a
single new file in `genres/` — no code changes.

---

## Categories at a glance

66 categories total, organized into 6 family-folder groups (Rekordbox
sidebar nesting) plus a flat collection of non-EDM genres and specials.

| Family folder | Subcategories | Notes |
|---|---|---|
| `Genres/House/` | 13 | House (≥2000), House Classics (<2000), Tech House, Minimal/Deep Tech, Progressive, Future, Melodic & Techno, Afro, Tropical, Organic, Bass House, Indie Dance, Nu Disco/Disco |
| `Genres/Techno/` | 3 | Peak Time, Raw/Deep, Hard |
| `Genres/Trance/` | 3 | Main Floor, Raw/Deep, Psy-Trance |
| `Genres/Bass/` | 7 | Bass/Club, 140-Deep Dubstep-Grime, Dubstep, Drum & Bass, UK Garage/Bassline, Trap/Future Bass, Breaks |
| `Genres/Other Electronic/` | 6 | Electro, Electronica, Downtempo, Ambient/Experimental, Hard Dance, Mainstage |
| `Genres/Regional/` | 6 | Europop, Eurodance, Amapiano, Afrobeats, Brazilian Funk, Latin Electronic |
| *(top-level)* | 28 | Pop (3 splits), Hip-Hop (2), Rock (4), Metal, Punk, R&B/Soul, Funk, Blues, Reggae, Lo-Fi, Country, Jazz, Classical, Folk, Christian/Gospel, Acoustic, Latin, World, Soundtrack, plus Acapellas / DJ Tools / Non-Music specials |

---

## Sources

- **Beatport** — DJ-shopping focused, EDM-heavy. Their 35-bucket structure
  is the spine. Beatport tags use literal compound names with
  slashes/parens (e.g. `Techno (Peak Time / Driving)`, `Dance / Pop`) —
  aliased verbatim so routing is 1:1.
- **Apple Music** — broader catalog covering Pop / Rock / Country / Jazz /
  Classical / regional. Their ~280 subgenres seed the non-EDM coverage.
- **Bandcamp** — flat 28-tag list. Sparse but useful for indie/
  experimental edge cases.

---

## Matching rules

### 1. Primary genre = longest matching alias wins (most-specific)

Aliases are matched case- and whitespace-insensitively. The *longest*
matched alias string wins. Examples:

- File tagged `"Tech House"`:
  - `tech house` alias → Tech House (9 chars).
  - `house` alias → House (5 chars).
  - Tech House wins (longer alias).

### 2. Year predicates gate matches

Categories with a `when:` clause require a `Year` tag and only match if
the year passes the predicate. Two categories often share aliases with
mutually-exclusive predicates:

- **Pop** vs **Pop (80s/90s)**: both alias `pop`. `Pop` requires
  `year_gte: 2000`; `Pop (80s/90s)` requires `year_lt: 2000`. A file
  tagged just `pop` with no year quarantines for manual handling.
- **House** vs **House Classics**: same pattern with year 2000 as the
  cutoff.

Shared aliases use the **list form** in the mapping file:

```yaml
pop: [Pop, Pop (80s/90s)]
"deep house": [House, House Classics]
```

### 3. Ties on alias length → AMBIGUOUS quarantine

If two categories both match an alias of the same length AND the same
candidate-token position, the file quarantines for manual review.

### 4. Secondary genres = remaining matches, ranked by specificity

After picking the primary, the categorizer keeps the other surviving
matches as *secondaries* (ranked by alias length). The
`playlists --genres` subcommand adds the track to up to
`rekordbox_playlist_fanout` playlists (default 3 = primary + 2).

If a track's tags map to the same category via multiple aliases (e.g.
`"deep house"` and `"house"` both → House), that counts once against the
fanout — de-duped by category, not by alias.

### 5. Decade playlist (separate axis)

`playlists --decades` derives the decade from the `Year` tag:
- Year < 2000 → 2-digit label (`70s`, `80s`, `90s`).
- Year ≥ 2000 → 4-digit label (`2000s`, `2010s`, `2020s`).

Skipped silently for files with no year tag.

---

## Filesystem vs Rekordbox naming

```yaml
- name: "Tech House"        # Rekordbox playlist display — human readable
  folder: Tech_House        # on-disk subdirectory — filesystem-safe
```

The display name is used verbatim as the Rekordbox playlist name; the
folder name is the on-disk subdir of `Library/Songs/`. Filesystem-unsafe
characters (slashes, parens) are stripped from folder names but kept in
display names so Rekordbox shows Beatport's literal bucket names (e.g.
`Techno (Peak Time / Driving)`) while the disk stays clean
(`Techno_PeakTime/`).

---

## How to add a category

1. Add the category to `genres.yaml`:
   ```yaml
   - name: "Hyperpop"
     folder: Hyperpop
   ```
2. Add at least one alias in `genres/generic.yaml`:
   ```yaml
   hyperpop: Hyperpop
   ```
3. Open a PR. CI validates that every alias points to a real category.

Lazy folder creation: the on-disk directory doesn't exist until a track
actually routes there.

## How to add a new tag source

```yaml
# src/musicsort/autoimport/genres/discogs.yaml
version: 1
source: Discogs
mappings:
  "house, deep house": House
  ...
```

All alias destinations must reference categories that exist in
`genres.yaml`.

---

## Contributing

PRs welcome for:
- New aliases (`genres/*.yaml`) when your store's tag isn't routing.
- New tag sources (`genres/<source>.yaml`) when a major store isn't covered.
- New categories (`genres.yaml`) when something genuinely doesn't fit.

Open an issue first for structural changes (new family, removed category,
match-rule change). Small alias additions can go straight to PR.
