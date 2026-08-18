# Kioskarr

A Radarr/Sonarr-style monitor for magazine (and best-effort newspaper) issues:
watch a list of publications, periodically search indexers for new issues,
grab them, and organize completed downloads into a library folder.

The core design constraint: **there is no TVDB/ComicVine equivalent for magazines** —
no canonical database of "issue X of title Y, released on date Z." So instead of
knowing episodes/issues ahead of time (the Sonarr model), this app searches
reactively against user-defined watched titles and fuzzy-matches whatever comes
back. Low-confidence matches land in a review queue rather than being guessed.

## Prerequisites

Kioskarr does not reimplement indexer scraping or torrent downloading — it
expects two other services already running, the same as any Servarr app:

- **[Prowlarr](https://github.com/Prowlarr/Prowlarr)** — indexer aggregation. Configure
  your Torznab/Newznab indexers there; Kioskarr calls Prowlarr's search API.
- **[qBittorrent](https://www.qbittorrent.org/)** with the WebUI enabled — download client.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate       # fish shell: source .venv/bin/activate.fish
pip install -e ".[dev]"
```

Configure via environment variables (or a `.env` file), all prefixed `KIOSKARR_`.
**Only `KIOSKARR_DATABASE_URL` is read on an ongoing basis** — you need a DB
connection before you can query it for anything else. Every other variable below
is used once, to seed the DB-backed settings row the very first time the app boots
against a fresh database; after that, edit them from the **Settings page** in the
UI instead (see Frontend below) — changes there are saved to the database and take
effect immediately, no restart or `.env` edit needed.

| Variable | Default | Seeds |
|---|---|---|
| `KIOSKARR_DATABASE_URL` | `sqlite:///./kioskarr.db` | *(always read from env — not a seed)* |
| `KIOSKARR_PROWLARR_URL` | `http://localhost:9696` | Prowlarr base URL |
| `KIOSKARR_PROWLARR_API_KEY` | *(empty)* | Prowlarr API key |
| `KIOSKARR_QBITTORRENT_URL` | `http://localhost:8080` | qBittorrent WebUI base URL |
| `KIOSKARR_QBITTORRENT_USERNAME` | `admin` | qBittorrent WebUI username |
| `KIOSKARR_QBITTORRENT_PASSWORD` | *(empty)* | qBittorrent WebUI password |
| `KIOSKARR_QBITTORRENT_DOWNLOADS_LOCAL_PATH` | *(empty)* | Local filesystem path where qBittorrent's own `save_path` is actually reachable from this process — e.g. a mount of a remote download directory. Needed for import to work unless kioskarr runs on the same host/filesystem as qBittorrent; see Import below. |
| `KIOSKARR_LIBRARY_ROOT` | `./library` | Default library root (publications also set their own `target_dir`) |
| `KIOSKARR_SEARCH_INTERVAL_HOURS` | `4` | How often to search for new issues |
| `KIOSKARR_IMPORT_INTERVAL_MINUTES` | `5` | How often to check for completed downloads |
| `KIOSKARR_MATCH_CONFIDENCE_THRESHOLD` | `75` | Fuzzy title-match score (0-100) required to auto-import |

## Run

With the venv activated:

```bash
uvicorn kioskarr.api.main:app --reload
```

If `uvicorn` isn't found, the venv likely isn't activated in your current shell —
either activate it first (see Setup above) or call it directly without activating:

```bash
.venv/bin/uvicorn kioskarr.api.main:app --reload
```

This starts the API, the single-page UI (served at `/`), and the background scheduler
(search + import jobs). Interactive API docs are at `http://localhost:8000/docs`. The app
always boots successfully even if Prowlarr/qBittorrent credentials aren't set yet — the
Settings page has to be reachable to configure them in the first place. Scheduler ticks and
the search-now/import-now actions each check for missing credentials individually and
skip/report clearly instead.

## Docker

Covers Kioskarr only — Prowlarr and qBittorrent are expected to already be running
somewhere reachable from the container, same as running it directly (see Prerequisites).

```bash
docker compose up -d --build
```

This builds the image, then bind-mounts two host directories (created automatically if
missing):

- `./data` → `/data` — holds `kioskarr.db`. **This is the one directory that actually
  matters to back up.**
- qBittorrent's real download directory → `/downloads` — change the `/path/to/your/downloads`
  host side in `docker-compose.yml` to wherever qBittorrent's `save_path` is actually
  reachable from this container (see Import below). Organized/renamed issues land in
  `/downloads/ebooks` (`KIOSKARR_LIBRARY_ROOT`) — a *subfolder of this same mount*, not a
  separate one, so the source download and the renamed copy are guaranteed to be on the
  same filesystem and importing actually hardlinks the file (no extra disk space, keeps
  seeding) instead of falling back to a plain copy. Set each publication's `target_dir` to
  a subfolder of this from the UI (e.g. `/downloads/ebooks/ouest-france`). If qBittorrent's
  downloads and your desired library location are genuinely on different filesystems, split
  this back into two separate bind mounts — it'll still work, just via a copy instead of a
  hardlink.

`KIOSKARR_PROWLARR_URL`/`KIOSKARR_QBITTORRENT_URL` default to
`http://host.docker.internal:9696`/`:8080` — reachable this way when Prowlarr/qBittorrent
run natively on the same host as this container (works out of the box on Docker Desktop;
the `extra_hosts` entry in `docker-compose.yml` is what makes `host.docker.internal`
resolve on Linux too). If they're *also* containers, join their Docker network directly
instead and reference them by container name — more reliable than routing through the
host, and doesn't require their WebUI ports to be published. Change the host-side port in
`ports:` too if 8000 is already taken on your setup.
Everything else (API keys, passwords, intervals) is a one-time seed exactly as described
in Setup above — configure the rest from the Settings page after first boot rather than
baking secrets into `docker-compose.yml`.

## Frontend

A single-page app (`kioskarr/static/index.html` + `app.js`) using [Alpine.js](https://alpinejs.dev/)
(vendored locally at `kioskarr/static/vendor/alpine.min.js` — no CDN dependency, no build step,
no bundler). It calls the JSON API below directly
via `fetch()` and routes client-side via the URL hash (`#/publications`, `#/review`, `#/grabs`,
`#/settings`), styled after Radarr/Sonarr's dark theme, deliberately without a calendar or cover art:

- **Publications** — list, add, edit, delete publications; toggle `monitored` in place;
  trigger `search-now`; view/reset a publication's cold-start `baseline_identifier`.
- **Review Queue** — the review queue, with an inline resolve form per item.
- **Grabs** — grab history, filterable by status.
- **Settings** — every setting below, editable and saved to the database live.

## Authentication

Matches Radarr/Sonarr's own model: **a single admin account**, no multi-user/roles.
There's no separate on/off setting — the app is open (no login at all) until you set
an admin password on the Settings page, and login becomes required for every page and
API request from that point on. Clear the password (a checkbox on the same page) to
disable login again. Sessions are a signed cookie (`kioskarr_session`); the signing key
is generated once and stored in the DB, so sessions survive restarts.

`GET /settings` never returns `prowlarr_api_key`, `qbittorrent_password`, or any
password — only `*_set` booleans so the UI can show configured/not-configured without
exposing values. `PATCH /settings` still accepts them (omit a field to leave it
unchanged, send a value to set it, send `""` to clear it). This is deliberately *not*
at-rest encryption of the SQLite file — same reasoning Radarr/Sonarr operate under:
anyone with file-level access to `kioskarr.db` likely also has process-level access to
the running app (which must read the plaintext to call Prowlarr/qBittorrent anyway), so
rely on normal file permissions instead.

## API

- `POST /publications` — add a publication to watch: `title`, `target_dir`, and optionally
  `type` (`magazine`/`newspaper`), `aliases` (alternate names uploaders use — punctuation like
  "Ouest-France" vs "Ouest France" doesn't need to match exactly, both sides are normalized
  the same way before comparing), `format_preference` (`pdf`/`cbr`/`any`), `min_seeders`,
  `monitored`, `grab_last_n` (see Cold start below).
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
- `GET /settings`, `PATCH /settings` — read/update every DB-backed setting (Prowlarr and
  qBittorrent connection details included, secrets never exposed — see Authentication
  below). Changing `search_interval_hours` or `import_interval_minutes` reschedules the
  background jobs immediately.
- `GET /auth/status` — `{auth_required, authenticated}`, always reachable. `POST /auth/login`
  — `{username, password}`, sets the session cookie. `POST /auth/logout` — clears it.
  Every other endpoint above requires a valid session once an admin password is set (see
  Authentication below).
- `GET /opds` — an [OPDS](https://specs.opds.io/opds-1.2) 1.2 catalog feed for external
  reader apps (Komga, Kavita, Calibre-web/COPS, or any OPDS client) — see OPDS below.

### OPDS (Komga/Kavita/Calibre-web/e-reader integration)

`GET /opds` is a navigation feed listing every publication; each links to
`GET /opds/publications/{id}`, an acquisition feed listing that publication's already-
imported issues, each with a download link (`GET /opds/issues/{id}/download`) serving the
real file with the correct `Content-Type` (`application/pdf`, `application/epub+zip`,
`application/vnd.comicbook+zip`/`-rar` for cbz/cbr). Point any OPDS client at
`http://<host>:8000/opds`.

OPDS clients are non-browser apps that can't do the session-cookie login flow used
elsewhere in this app, so `/opds/*` accepts **either** a valid session cookie **or** HTTP
Basic Auth (same admin username/password) once one is set — everything else in the API
still requires the session specifically.

**Token-based access** (`/opds/token/{token}/...`, mirroring every route above) exists for
clients that can't answer an interactive auth challenge at all — just a bare URL with no
way to prompt for credentials (e.g. Mihon's Kavita extension, repurposed as a generic OPDS
client, since Mihon itself has no built-in generic OPDS support). The token is visible on
the Settings page as a ready-to-paste URL, with **Copy** (clipboard), **QR** (a scannable
code for mobile apps — generated entirely client-side via the vendored
`kioskarr/static/vendor/qrcode.js`, the URL never leaves the browser), and **Reload**
(`PATCH /settings` with `{"regenerate_opds_token": true}` — generates a new token,
invalidating the old URL immediately) buttons next to it. Anyone with the token URL has
full read access to your library, no password needed, so treat it like one.

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

### Choosing among multiple candidates

Prowlarr aggregates multiple indexers, so the same issue can come back as more than
one distinct candidate — confirmed live, the same date from two different trackers.
Rather than grabbing every matching candidate (which would download the same issue
twice), exactly one is picked per identifier: first by indexer priority (the trust
order already configured in Prowlarr itself — lower priority number wins), then by
seeders as a tiebreaker (a healthier swarm downloads more reliably). Prowlarr's search
API doesn't sort results itself (confirmed empirically — results come back in
whatever order the indexer returns, not by date), so this ranking is done client-side.

Prowlarr's search response also includes each release's info-hash directly — captured
and used to check for a duplicate *before* even asking qBittorrent to add it (rather
than only finding out afterward from a missing hash), and to skip the polling entirely
for indexers that provide it.

### Import (making the downloaded file reachable)

The import job reads the completed file directly off disk — `torrent["save_path"]`
(as qBittorrent's API reports it) plus the file name, then hardlinks or copies it into
the publication's `target_dir`. That only works if this process can actually see
qBittorrent's download directory on its own filesystem. If qBittorrent runs elsewhere
(a different host, a NAS, etc.) and you've mounted its download directory locally at a
different path, set `KIOSKARR_QBITTORRENT_DOWNLOADS_LOCAL_PATH` to that local path —
import will use it instead of trusting the API's `save_path` directly. Hardlinking
(no extra disk space, keeps seeding) only succeeds if `target_dir` is on the *same*
filesystem as that downloads path; otherwise it falls back to a plain copy automatically.

**Duplicate torrents**: if a release turns out to already be a torrent qBittorrent has
elsewhere (matched by info-hash — known upfront from Prowlarr's response when available,
otherwise detected after the fact from no new hash appearing), it's flagged for review
immediately instead of being left stuck at "downloading" forever with nothing to import.
Resolve it by finding the existing file in qBittorrent and passing its path as
`file_path` to `POST /review/{id}/resolve`.

**Multi-file torrents**: a torrent isn't assumed to contain exactly one issue, and the
right file isn't found by size. Only recognized magazine/book file types (`pdf`, `epub`,
`cbr`, `cbz`, `mobi`) are ever considered — a cover image or NFO is never a candidate
regardless of size — and each candidate is parsed and confidence-matched by name against
the publication, the same way search does. Two real release shapes confirmed during
testing are both handled: a torrent bundling several issues of the *same* publication
(a "12-month archive" — files confidently match with different identifiers) and a torrent
bundling one issue each of several *different* publications for one date (a "national
newspapers" bundle — only the file whose name confidently matches this publication is a
candidate at all). Either way, if more than one distinct issue is a genuine confident
match, that's flagged for review rather than silently importing one and discarding the
rest. If there's more than one candidate file and *none* confidently matches, it's also
flagged rather than guessed — falling back to "the largest file" would risk importing an
entirely unrelated publication's issue mislabeled as this one when a torrent bundles
several different publications.

For a folder-based multi-file torrent, qBittorrent reports each file's name with its
folder prefix (`"Some Release Folder/actual-file.pdf"`), and a release folder's own name
often carries its own date — confirmed live, a "Journaux Nationaux du Mardi 12 Août 2025"
folder. Matching is always done against the basename only, never the full path, or the
folder's own date/title would get picked up instead of the actual file's.

**Skipping unwanted files entirely**: rather than downloading a whole bundle and sorting
it out at import time, as soon as a torrent is grabbed its file list is checked — if
exactly one file is an unambiguous, confident match, every other file in that torrent is
set to qBittorrent priority 0 (do not download) immediately. Confirmed live against a
real "Journaux Nationaux" torrent bundling 12 different French dailies (~200MB total):
only the matched publication's file (~12MB) actually downloads, the other 11 are skipped
entirely — worth doing not just for bandwidth/disk, but because downloading (and then
seeding) 11 newspapers you never wanted burns ratio on a private tracker for nothing.
This only ever restricts when there's a single confident answer; an ambiguous or
unmatched torrent downloads everything, so import-time review still has full access to
every file.

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
- Matching is heuristic (regex + fuzzy title score), not lookup-based — expect some false
  positives/negatives; that's what the review queue is for.

## Legal note

This tool automates searching and downloading of torrents, same as Radarr/Sonarr — you are
responsible for ensuring your use complies with applicable copyright law and the terms of
whatever indexers/trackers you connect it to.
