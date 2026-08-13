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
API docs are at `http://localhost:8000/docs`.

## API

- `POST /publications` — add a publication to watch: `title`, `target_dir`, and optionally
  `type` (`magazine`/`newspaper`), `aliases` (alternate names uploaders use), `format_preference`
  (`pdf`/`cbr`/`any`), `min_seeders`, `monitored`.
- `GET /publications`, `GET/PATCH/DELETE /publications/{id}`
- `POST /publications/{id}/search-now` — trigger an immediate search (useful for testing)
- `GET /grabs` — history of what's been grabbed and its status
- `GET /review` — items that couldn't be confidently auto-matched
- `POST /review/{id}/resolve` — manually assign a review item to a publication + issue identifier,
  which imports it into that publication's library folder

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
- Newspaper support is best-effort and unproven — real-world torrent availability and naming
  consistency for newspapers is much weaker than for magazines.
- No frontend UI yet — operate the review queue via the API/docs.
- Matching is heuristic (regex + fuzzy title score), not lookup-based — expect some false
  positives/negatives; that's what the review queue is for.

## Legal note

This tool automates searching and downloading of torrents, same as Radarr/Sonarr — you are
responsible for ensuring your use complies with applicable copyright law and the terms of
whatever indexers/trackers you connect it to.
