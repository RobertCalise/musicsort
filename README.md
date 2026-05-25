# musicsort

Background filesystem watcher that auto-routes audio files into a genre-organized music library, with content-aware deduplication and quality-upgrade replacement. Drop a track into `~/Music/AutoImport/`, it lands in `~/Music/Library/Songs/<Genre>/` within seconds. Inspired by Serato's AutoImport folder behavior.

macOS-focused (uses FSEvents + LaunchAgents); the core routing works on Linux but the `install` / `uninstall` service commands are macOS-only.

## What it does

For each file that arrives in the watched folder:

1. **Read tags** (ID3 / MP4 / Vorbis) — title, artist, genre, year, BPM, etc.
2. **Categorize by genre** against a YAML taxonomy with multi-delimiter tokenization (handles Apple's `Dance / Pop`, Beatport's `Bass & Garage`, parenthetical sub-genres like `Trance (Main Floor)`, comma lists).
3. **Dedup** — SHA256 byte-identity first; chromaprint fingerprint (via `fpcalc`) for cross-encoding matches.
4. **Quality compare** on fingerprint matches — `(codec_lossless, bitrate, sample_rate, channels, size)` tuple comparison. Better encoding wins, displaces the worse one to quarantine.
5. **Route** to `Songs/<Genre>/`, or quarantine to `_Unsorted/<reason>/` if any step fails (no genre tag, unknown genre, ambiguous, manual-only, unreadable, duplicate, worse-quality).

Outcomes are recorded in a JSONL audit log (`_Unsorted/unsorted.log`) and a fingerprint + Rekordbox-queue cache (`<library_root>/.musicsort/fingerprints.db`).

## Install

Requires Python 3.12+, [uv](https://docs.astral.sh/uv/), and [chromaprint](https://acoustid.org/chromaprint) (provides `fpcalc`).

```sh
brew install chromaprint ffmpeg
git clone https://github.com/RobertCalise/musicsort.git
cd musicsort
uv sync
```

Verify:

```sh
uv run musicsort --help
uv run pytest        # 200 tests should pass
```

## Quickstart

Drop a tagged audio file into the watched folder:

```sh
mkdir -p ~/Music/AutoImport ~/Music/Library
cp ~/Downloads/some_tagged_track.mp3 ~/Music/AutoImport/
uv run musicsort once
```

Output: `~/Music/Library/Songs/<Genre>/some_tagged_track.mp3` if the genre tag matched the taxonomy, or under `~/Music/Library/_Unsorted/<reason>/` if it didn't.

## Run as a background service (macOS)

To have the watcher run continuously and auto-start on login:

```sh
uv run musicsort install     # registers a LaunchAgent
uv run musicsort status      # check it's running
uv run musicsort uninstall   # stop + remove
```

Logs land at `~/Library/Logs/musicsort.out.log` / `musicsort.err.log`.

## Subcommands

| Command | Purpose |
|---|---|
| `musicsort once` | Process all files currently in AutoImport, exit |
| `musicsort inspect` | Dry-run preview: show what `once` would do |
| `musicsort watch` | Long-running watcher (foreground); use `install` to background it |
| `musicsort audit` | Scan the existing library for duplicates, mis-shelved tracks, bad tags |
| `musicsort install` | Install the watcher as a macOS LaunchAgent |
| `musicsort uninstall` | Stop and remove the LaunchAgent |
| `musicsort status` | Show LaunchAgent state |

## Configuration

All settings are env vars prefixed `MUSICSORT_`. Defaults are in `src/musicsort/config.py`.

The master knob is `MUSICSORT_LIBRARY_ROOT` (default `~/Music/Library`). Internal paths (`songs_dir`, `quarantine_dir`, `fingerprint_db_path`) derive from it automatically — override the root and the rest follow. Each derived path can be overridden independently if you want a non-standard layout.

| Variable | Default | Purpose |
|---|---|---|
| `MUSICSORT_LIBRARY_ROOT` | `~/Music/Library` | Library base; drives subpaths |
| `MUSICSORT_AUTOIMPORT_FOLDER` | `~/Music/AutoImport` | Watcher input (independent) |
| `MUSICSORT_SONGS_DIR` | `<library_root>/Songs` | Routed-track destination |
| `MUSICSORT_QUARANTINE_DIR` | `<library_root>/_Unsorted` | Failed-route destination |
| `MUSICSORT_FINGERPRINT_DB_PATH` | `<library_root>/.musicsort/fingerprints.db` | SHA + chromaprint cache |
| `MUSICSORT_SIMILARITY_THRESHOLD` | `0.95` | Chromaprint match cutoff |
| `MUSICSORT_WATCH_SETTLE_SECONDS` | `2.0` | Settle time before processing |

See `.env.example` for the full list. Drop a `.env` at the repo root to override.

## Taxonomy

Genre-to-folder routing rules are split across two layers:

- `src/musicsort/autoimport/genres.yaml` — the category catalog. Each category declares its canonical name, target folder, optional `family` (for Rekordbox sidebar grouping), optional `when:` clause (e.g. year predicates), and the source-keyed alias mappings it accepts.
- `src/musicsort/autoimport/genres/{generic,beatport,apple,bandcamp}.yaml` — per-source alias dictionaries. A category opts into a source's alias by referencing the dictionary key under that source. Aliases may be a single string or a list of strings; the same dictionary key can appear under multiple categories when a `when:` clause splits them by year (e.g. `Pop` vs. `Pop (80s/90s)` both consume the `pop` alias and the year predicate picks the winner).

Year-based disambiguation routes a tag like `Pop` to `Pop_80s_90s` when year < 2000 and to `Pop_Modern` otherwise. Files tagged with a year-gated alias but missing a year tag land in `_Unsorted/missing_year/` so the fix (add a year tag) is obvious.

Edit the YAML to add categories or aliases for your library; reload by restarting the watcher. See [docs/taxonomy.md](docs/taxonomy.md) for the matching rules and contribution guide.

## Limitations / non-goals

- **macOS-only for the service commands and the Rekordbox integration.** The routing/dedup library code is cross-platform; the LaunchAgent install/uninstall and the `rekordbox` subcommands are macOS-specific (pyrekordbox + Rekordbox 6/7's encrypted `master.db`).
- **No-genre files don't auto-route.** Tracks without an ID3 genre tag land in `_Unsorted/no_genre/`. AcoustID/MusicBrainz lookup as a fallback is a planned future feature; for now, retag manually.
- **No Serato / Traktor integration.** musicsort routes files on disk and, optionally, into Rekordbox's collection + playlists (see `musicsort rekordbox --help`). Serato/Traktor are out of scope.
- **No GUI.** CLI only.

## Development

```sh
uv sync                  # install deps + venv at .venv/
uv run pytest            # full test suite
uv run musicsort --help  # CLI

pre-commit install       # ruff + hygiene on commit
pre-commit run --all-files
```

## License

MIT — see [LICENSE](LICENSE).
#musicsort
