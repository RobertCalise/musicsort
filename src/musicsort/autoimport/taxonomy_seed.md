# Genre Routing Rules

**Purpose:** the playbook for deciding which folder an incoming track belongs in. Living document — append decisions and definitions as they're made.

**Read `PROJECT.md` first** for library philosophy and guardrails.

---

## Routing protocol

For every incoming track that needs classification:

1. **Read available signals first.** Filename, folder it came from (if part of a pack), ID3 tags (artist, title, genre, BPM, key, comments), any accompanying notes. Don't play the audio — you can't.
2. **Match against the definitions below.** If one folder is a clear fit, propose it.
3. **If ambiguous between two or more folders:**
   - Note the candidates and what's pulling toward each.
   - Research industry consensus if needed (web search for "is [artist - track] [genre A] or [genre B]", Beatport/Discogs genre tagging, RA reviews).
   - Propose a primary recommendation with reasoning, plus the alternative(s).
   - Wait for my approval. Don't guess.
4. **If no folder fits cleanly,** quarantine to `Unsorted_Music/_Review/` and flag for my review. Better to defer than to mis-shelve.
5. **Log the move** per `WORKFLOWS.md`.

**Tiebreaker hierarchy when I'm not available to approve and a call must be made:**
1. Sonic identity (what does it sound like, structurally) over marketing label.
2. Where I'd most likely *play* it in a set (energy, role) over pure taxonomy.
3. BPM as a supporting signal, not a decider — many genres overlap in BPM.
4. When still tied: pick the broader folder over the narrower one. A house track in `Music_By_Genre/House/` that should have been in `Music_By_Genre/Tech_House/` is a cheaper error to fix than the reverse.

**Note on paths in this file:** genre names below are unqualified (e.g. `House`, `Techno`) for readability. The actual paths are `Music_By_Genre/House/`, `Music_By_Genre/Techno/`, etc.

## Genre definitions

Working definitions. Fill in over time. When a definition is missing or thin and a routing decision needs it, research and propose an addition.

### `House`
- **Working definition:** house music, broad. The default landing zone for house-family tracks that aren't clearly tech house or classics-era.
- **Era convention:** modern house lives here. Classic-era house (rough cutoff TBD — see `House_Classics`) goes in `House_Classics/`.
- **Distinguishing from `Tech_House`:** tech house typically has stripped-back, percussive, tech-leaning production with a driving low-end groove; "house" proper is more melodic/vocal/song-oriented. When unsure, research the specific track's tagging on Beatport.
- **Distinguishing from `Bass`:** bass house has heavy, distorted, bass-forward drops more aligned with the UK bass family — those go to `Bass/`.
- **Distinguishing from `EDM`:** if it's festival-mainstage big-room energy with house elements but not really house-cultural, `EDM/`.

### `House_Classics`
- **Working definition:** classic house, primarily late 80s through early 2000s.
- **Era cutoff:** TBD. Propose a year boundary on first contested case (initial guess: ~2000, but verify with industry convention).
- **Examples already shelved:** Modjo, Stardust, Daft Punk, Robin S, CeCe Peniston, Crystal Waters, Black Box, Sandy Rivera.

### `Tech_House`
- **Working definition:** tech house — percussive, groove-driven, low-end-forward, less melodic than house proper.
- **BPM rough range:** typically 120–128.
- **Distinguishing from `Techno`:** techno is darker, more linear, less swung, typically 125–135+ with less vocal/hook focus.

### `Techno`
- **Working definition:** techno. Linear, driving, often darker and more industrial than house/tech house.
- **BPM rough range:** 125–140+ depending on sub-style.
- **Note:** if peak-time / festival techno crosses into mainstage territory, still `Techno/` — `EDM/` is for non-techno-cultural mainstage stuff.

### `Trance`
- **Working definition:** trance. Melodic, euphoric or progressive, longer build/breakdown structures.
- **BPM rough range:** 128–140, with sub-styles (progressive trance lower, uplifting/psy higher).
- **Examples already shelved:** Tiësto, Paul van Dyk, Above & Beyond, Armin van Buuren.

### `EDM`
- **Working definition:** big-room, festival, mainstage electronic dance. Catch-all for high-energy electronic that doesn't sit cleanly in a more specific genre.
- **Note:** this is partially a "doesn't fit elsewhere" bucket for the mainstage family. Tracks that *do* fit a more specific genre (trance, tech house, etc.) go there even if they're festival-sized.

### `Bass`
- **Working definition:** UK bass family — bass house, bassline, and adjacent heavy-bass styles.
- **Scope (working):** lumps bass house + bassline. Other bass-heavy styles (dubstep, riddim, future bass) — TBD on first contact; propose a split or include based on what arrives.
- **Distinguishing from `House`:** if the track's identity is "heavy bass-forward drop," it's `Bass/`, even if it has house tempo and structure.

### `DnB`
- **Working definition:** drum & bass. ~165–180 BPM, breakbeat-driven.

### `UK_Garage`
- **Working definition:** UK garage — 2-step, speed garage, and the broader UKG family. Crossfader's "Garage Vibes" marketing label maps here.
- **BPM rough range:** ~130 (2-step) up to ~140 (speed garage).

### `Eurodance`
- **Working definition:** Eurodance and classic dance-pop, primarily 90s–early 2000s. High-energy, vocal-driven, often cheesy in the best way.
- **Examples already shelved:** Haddaway, La Bouche, Cascada, Eiffel 65, Basshunter, Darude, ATB, Alice DeeJay, Snap!, Corona, Stromae.

### `Hard_Dance`
- **Working definition:** hardstyle, hands-up, and hard dance proper. Dutch-origin family, ~140–155 BPM, distorted "hardstyle" kicks, reverse-bass patterns, often melodic builds. Beatport groups these together under "Hard Dance / Hardcore."
- **BPM rough range:** ~140–155 (hands-up lower end, hardstyle/euphoric higher).
- **Distinguishing from `Techno`:** **hard techno is NOT here** — it lives in `Techno/`. Hard techno is dark, industrial, schranz-influenced; sonically and culturally techno. Hard dance is melodic, supersaw-driven, festival-mainstage.
- **Distinguishing from `EDM`:** if a track is mainstage-energy but has the distinctive hardstyle kick or hands-up euphoria, it goes here. Generic big-room mainstage goes to `EDM/`.
- **Examples already shelved (initial):** AC13, Kyanu, R3HAB x Da Tweekaz.
- **Split criteria:** when track count justifies, split into sub-folders (e.g. `Hardstyle/`, `Hands_Up/`). Lumped for now.

### `Hip_Hop_Trap_Afrobeats`
- **Working definition:** placeholder lump for hip-hop, trap, and afrobeats.
- **Split criteria:** when any one of the three sub-categories accumulates ~15–20 distinct tracks, propose splitting into `Hip_Hop/`, `Trap/`, `Afrobeats/`. Current count noted in `crate_verification.md` Phase 3 (14 at time of lump).

### `Pop_80s_90s`
- **Working definition:** pop from the 80s and 90s. Era-tagged to mirror `House_Classics` convention.

### `Pop_Modern`
- **Working definition:** modern pop, roughly 2000s onward.
- **Cutoff:** boundary with `Pop_80s_90s` is era-based; propose a year on first contested case (suggested: 2000).

### `Pop_Punk`
- **Working definition:** pop-punk.

### `Piano_Rock`
- **Working definition:** piano-driven rock.

### `Acapellas`
- **Working definition:** vocal-only tracks (no instrumental backing).
- **Routing rule:** **manual curation only.** Never auto-route incoming files here. If a file looks like an acapella, quarantine to `Unsorted_Music/_Review/` and flag.

### `Samples` (no longer under `Music_By_Genre/` as of 2026-05-16)
- **Samples moved to `Samples/` at the library root**, peer to `Music_By_Genre/`. Subdivided by use case into `DJ_FX/`, `Production_Packs/`, `Course_Materials/`. See `PROJECT.md` Samples section for canonical structure.
- **Routing rule:** **manual curation only.** Never auto-route incoming files into any `Samples/` subfolder. If a file looks like a sample or part of a pack, quarantine to `Unsorted_Music/_Review/` and flag.

## Decisions log

Append-only. Format: `YYYY-MM-DD — decision — reasoning`.

- `2026-05-13` — initial `GENRE_RULES.md` drafted from `crate_verification.md` Phase 3 conventions and existing folder taxonomy.
- `2026-05-13` — created `Hard_Dance/` folder (initial 3 tracks: AC13, Kyanu, R3HAB x Da Tweekaz). Reasoning: Beatport groups hardstyle/hands-up/hard dance together; warrants its own folder rather than `EDM/` catch-all once it has dedicated tracks.
- `2026-05-13` — **hard techno stays in `Techno/`**, not `Hard_Dance/`. Routing of KILL SCRIPT — GRAVITY → `Techno/` per existing rule ("if peak-time / festival techno crosses into mainstage territory, still `Techno/`"). Hard techno is sonically/culturally techno (dark, industrial, schranz) — distinct from the Dutch hardstyle family.
- `2026-05-13` — **library restructured**: all genre folders moved under `Music_By_Genre/`, `Unsorted/` renamed `Unsorted_Music/`, `Docs` symlink renamed to `Documents`. Paths in this file updated; historical move log entries in `move_log.md` preserve old paths.
- `2026-05-13` — `Documents` symlink at library root **removed**. Educational PDFs still live at `~/Documents/DJ_Materials/` but are no longer linked from inside the library.
- `2026-05-16` — **library root moved** from `~/Music/DJ_Library/` to `~/DJ_Library/`. Reason: avoid Music.app / iCloud Music Library interference, and prepare for eventual NAS master + local working dir split.
- `2026-05-16` — **Samples promoted** to top-level peer of `Music_By_Genre/` (previously at `Music_By_Genre/Samples/`). Samples aren't a genre, so they shouldn't be inside `Music_By_Genre/`. Subdivided into `Samples/DJ_FX/` (live trigger samples), `Samples/Production_Packs/` (DAW material), `Samples/Course_Materials/` (educational/practice).
- `2026-05-16` — **sample pack folder names normalized** to snake_case. Stripped vendor hash suffixes from illements pack names (e.g., `illements-808-Bass_2026-05-06-214459_bnah` → `illements_808_Bass`). Removed trailing spaces from Tomorrowland Academy pack names. Vendor-recognizable names preserved.
- `2026-05-16` — **source-based inbox pattern documented**: top-level `Crossfader/`, `Tomorrowland/` folders are *staging only* for incoming downloads from those sources, not permanent organizational layers. Full tracks get sorted into `Music_By_Genre/` once classified. See `PROJECT.md`.

## Open questions for future resolution

Append as they come up; resolve and move to decisions log when decided.

- `House_Classics` era cutoff year — currently undefined.
- `Pop_80s_90s` / `Pop_Modern` cutoff year — currently undefined.
- `Bass` scope — does it include dubstep / riddim / future bass, or do those get their own folder when they arrive?
- `Hip_Hop_Trap_Afrobeats` split — pending content growth.
