# Magazinerr

A Radarr/Sonarr-style monitor for magazine (and best-effort newspaper) issues:
watch a list of publications, periodically search indexers for new issues,
grab them, and organize completed downloads into a library folder.

The core design constraint: **there is no TVDB/ComicVine equivalent for magazines** —
no canonical database of "issue X of title Y, released on date Z." So instead of
knowing episodes/issues ahead of time (the Sonarr model), this app searches
reactively against user-defined watched titles and fuzzy-matches whatever comes
back. Low-confidence matches land in a review queue rather than being guessed.

## Prerequisites

Magazinerr does not reimplement indexer scraping or torrent downloading — it
expects two other services already running, the same as any Servarr app:

- **[Prowlarr](https://github.com/Prowlarr/Prowlarr)** — indexer aggregation. Configure
  your Torznab/Newznab indexers there; Magazinerr calls Prowlarr's search API.
- **[qBittorrent](https://www.qbittorrent.org/)** with the WebUI enabled — download client.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate       # fish shell: source .venv/bin/activate.fish
pip install -e ".[dev]"
```

Configure via environment variables (or a `.env` file), all prefixed `MAGAZINERR_`:

| Variable | Default | Description |
|---|---|---|
| `MAGAZINERR_DATABASE_URL` | `sqlite:///./magazinerr.db` | SQLAlchemy DB URL |
| `MAGAZINERR_PROWLARR_URL` | `http://localhost:9696` | Prowlarr base URL |
| `MAGAZINERR_PROWLARR_API_KEY` | *(required)* | Prowlarr API key |
| `MAGAZINERR_QBITTORRENT_URL` | `http://localhost:8080` | qBittorrent WebUI base URL |
| `MAGAZINERR_QBITTORRENT_USERNAME` | `admin` | qBittorrent WebUI username |
| `MAGAZINERR_QBITTORRENT_PASSWORD` | *(required)* | qBittorrent WebUI password |
| `MAGAZINERR_QBITTORRENT_DOWNLOADS_LOCAL_PATH` | *(unset)* | Local filesystem path where qBittorrent's own `save_path` is actually reachable from this process — e.g. a mount of a remote download directory. Required for import to work unless magazinerr runs on the same host/filesystem as qBittorrent; see Import below. |
| `MAGAZINERR_LIBRARY_ROOT` | `./library` | Default library root (publications also set their own `target_dir`) |
| `MAGAZINERR_SEARCH_INTERVAL_HOURS` | `4` | How often to search for new issues |
| `MAGAZINERR_IMPORT_INTERVAL_MINUTES` | `5` | How often to check for completed downloads |
| `MAGAZINERR_MATCH_CONFIDENCE_THRESHOLD` | `75` | Fuzzy title-match score (0-100) required to auto-import |

## Run

With the venv activated:

```bash
uvicorn magazinerr.api.main:app --reload
```

If `uvicorn` isn't found, the venv likely isn't activated in your current shell —
either activate it first (see Setup above) or call it directly without activating:

```bash
.venv/bin/uvicorn magazinerr.api.main:app --reload
```

This starts the API and the background scheduler (search + import jobs). Interactive
API docs are at `http://localhost:8000/docs`. The app fails fast at startup with a
clear error if `MAGAZINERR_PROWLARR_API_KEY` or `MAGAZINERR_QBITTORRENT_PASSWORD` is
missing, rather than booting fine and failing silently later in the background scheduler.

## API

- `POST /publications` — add a publication to watch: `title`, `target_dir`, and optionally
  `type` (`magazine`/`newspaper`), `aliases` (alternate names uploaders use), `format_preference`
  (`pdf`/`cbr`/`any`), `min_seeders`, `monitored`, `grab_last_n` (see Cold start below).
- `GET /publications`, `GET/PATCH/DELETE /publications/{id}` — `PATCH` also accepts
  `baseline_identifier` to manually set/reset the cold-start floor (see below).
- `POST /publications/{id}/search-now` — trigger an immediate search (useful for testing)
- `POST /jobs/import-now` — trigger the import job immediately instead of waiting for the
  next scheduled tick (useful for testing)
- `GET /grabs` — history of what's been grabbed and its status
- `GET /review` — items that couldn't be confidently auto-matched, including grabs that turned
  out to be a duplicate of a torrent qBittorrent already had (see below)
- `POST /review/{id}/resolve` — manually assign a review item to a publication + issue identifier,
  which imports it into that publication's library folder. Also accepts an optional `file_path`
  to override the source path recorded at review time — required for duplicate-torrent items,
  since their real file location isn't known automatically; find it in qBittorrent and pass it here
- `GET /search/preview?query=...` — read-only: hits Prowlarr and shows what the parser/matcher
  would see for a query, without grabbing anything or writing to the database. Useful for
  checking indexer coverage and parse quality for a title before adding it as a publication.

### Cold start (avoiding a back-catalog dump on day one)

An indexer can return a publication's entire available history — a single "le monde"
or "ouest france" search can turn up 80+ historical issues. Since a freshly-added
publication owns nothing yet, every one of them would otherwise look "new" and get
grabbed. Instead, the very first search for a publication only grabs its `grab_last_n`
most recent eligible releases (default `1`), then permanently records a
`baseline_identifier` floor — nothing at or below it is ever grabbed again, on this
or any future search, even if an old release resurfaces (e.g. a reseed). You can see
and manually override that floor via `PATCH /publications/{id}` — set
`baseline_identifier` to a specific value to say "only monitor issues after this one,"
or clear it to `null` to force a cold-start re-evaluation.

### Import (making the downloaded file reachable)

The import job reads the completed file directly off disk — `torrent["save_path"]`
(as qBittorrent's API reports it) plus the file name, then hardlinks or copies it into
the publication's `target_dir`. That only works if this process can actually see
qBittorrent's download directory on its own filesystem. If qBittorrent runs elsewhere
(a different host, a NAS, etc.) and you've mounted its download directory locally at a
different path, set `MAGAZINERR_QBITTORRENT_DOWNLOADS_LOCAL_PATH` to that local path —
import will use it instead of trusting the API's `save_path` directly. Hardlinking
(no extra disk space, keeps seeding) only succeeds if `target_dir` is on the *same*
filesystem as that downloads path; otherwise it falls back to a plain copy automatically.

**Duplicate torrents**: if the release qBittorrent was asked to add turns out to already
be a torrent it has elsewhere (matched by info-hash), no *new* torrent is created — the
grab is immediately flagged for review instead of being left stuck at "downloading"
forever with nothing to import. Resolve it by finding the existing file in qBittorrent
and passing its path as `file_path` to `POST /review/{id}/resolve`.

**Multi-file torrents**: a torrent isn't assumed to contain exactly one issue. If more
than one file looks substantial (bigger than ~1MB and at least 10% of the largest file's
size — covers/NFOs don't count, but a real bundled second issue does), it's flagged for
review rather than silently picking the largest and discarding the rest. This is a real
release shape, not hypothetical: a genuine "annual archive" release bundling 12 separate
monthly issues plus an NFO in one torrent was found and confirmed during testing.

## Testing

```bash
pytest
```

Parser/matcher tests are table-driven against realistic release-name shapes (see
`tests/test_parser.py`) — this is the highest-risk part of the system since there's no
canonical metadata to validate against, so it's covered independently of any live
Prowlarr/qBittorrent instance.

## Known limitations (by design, for MVP)

- No quality-profile "upgrade" logic — first release that matches and meets `min_seeders` wins.
- Single download client (qBittorrent) and a single indexer path (Prowlarr's search API).
- A release spanning two calendar days (a combined weekend edition, e.g. "Du 9 10 Mai
  2026") is assigned the identifier of the later day only — there's no clean single-identifier
  representation for a two-day span. Hasn't caused a real collision in testing so far.
- No frontend UI yet — operate the review queue via the API/docs.
- Matching is heuristic (regex + fuzzy title score), not lookup-based — expect some false
  positives/negatives; that's what the review queue is for.

## Legal note

This tool automates searching and downloading of torrents, same as Radarr/Sonarr — you are
responsible for ensuring your use complies with applicable copyright law and the terms of
whatever indexers/trackers you connect it to.
