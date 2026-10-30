# JSON API, CLI and Claude Code skill

Machine-readable endpoints are served on the dashboard port under `/api/`, authorized per request with a bearer token (never the dashboard session cookie). Two tokens: the read-only `API_TOKEN` opens the video library only, the `ADMIN_TOKEN` opens every `/api/` route. Success returns a bare object or array. Failure returns `{"error": "<short reason>"}` with a 4xx/5xx status, usually with a human-readable `detail` and sometimes more fields (`candidates`, `playlists`, `active`, `intent`); a rejected bearer is logged once (client IP, method, path) and answered `401 {"error": "unauthorized"}`. `GET /api/openapi.json` is the authoritative description (`tests/test_openapi.py` keeps it in step with the routes).

API requests are exempt from the dashboard's cross-site `Origin` check: there is no cookie to ride.

## Read-only (`API_TOKEN` or `ADMIN_TOKEN`)

Entry key names mirror a sidecar-built library index (`id`, `title`, `channel`, `channelId`, `duration`, `uploadDate`, `tags`, `categories`, `description`, `thumbnail`, `chapters` as `[{start, title}]` with `start` in seconds, `uploadTimestamp` (the upload instant in epoch seconds, `null` when YouTube publishes only a date) and `fetchedAt` in epoch seconds), so index consumers can parse them unchanged; `webpageUrl` and `exported` (file moved to the export folder / media library) are added.

- `GET /api/videos` — all videos with a cached `file_id`, deduplicated by YouTube id, newest upload first. Supports `?exported=true|false` and `ETag`/`If-None-Match` (`304 Not Modified` when unchanged). The body and its tag are rendered once per state revision and cached, so a client polling with `If-None-Match` costs the bot a dictionary lookup. Rows stored before metadata enrichment (no `video_id`) are skipped
- `GET /api/videos/{id}` — a single entry by YouTube video id, `404` when unknown
- `GET /api/openapi.json` — OpenAPI 3.1 description of every `/api` route, readable with either token

```bash
curl -H "Authorization: Bearer $API_TOKEN" http://localhost:8080/api/videos?exported=true
```

## Admin (`ADMIN_TOKEN`)

### Reads

Served by both nodes of an HA pair (each describes itself).

- `GET /api/status` — the `/health` fields (`role`, `node`, `ready`, `rev`, …) plus `counts` (`users`, `banned`, `videos` deduplicated as the videos API serves them, `videos_rows`, `subscriptions`, `subscribers`, `intents: {pending, retrying, ignored, total}`, `inline_queries`, `alerts`), `tasks` (background task name → last run or `null`), `backup: {dir, newest_age_sec}`, `migration`, `queue_size`, `vpn` (see below; `null` without `VPN_CONTROL_APIKEY`) and `node_time`
- `GET /api/users?search=&banned=true|false` — `[{id, name, username, banned, banned_at, requests, last_at}]`, most requests first; `search` matches id, name and username (with or without `@`) case-insensitively
- `GET /api/subscriptions?chat_id=` — `[{url, title, uploader, uploader_videos, chat_ids: [{id, name}]}]`, optionally only the given subscriber's; `chat_id` is a numeric id or `@username` (`404 unknown user` with `candidates`, `409 ambiguous user`)
- `GET /api/intents?ignored=true|false` — `[{url, status, priority, retries, source, title, upload_date, chat_ids, inline_count, message_count, retry_in_sec}]` by priority; `status` is `pending`, `retrying` (failed before or backing off) or `ignored`
- `GET /api/intents?url=` — one URL's `{url, status, intent}`: the entry above while queued, `delivered` (popped, file cached) or `absent` (never queued or dropped) with `intent: null`
- `GET /api/alerts` — `[{id, level, message, since}]`, the dashboard health banner
- `GET /api/playlists?url=` — what `/subscribe` would offer for a video, playlist or channel URL: `{uploader, uploader_url, uploader_videos, playlists: [{title, url}], direct, fallback_videos}`; `400` for a non-http or unsupported URL, `502 {error: "extraction failed", detail}` when yt-dlp fails
- VPN status: with `VPN_CONTROL_APIKEY` set, the bot polls the gluetun control server every 60 s on both nodes and `/api/status` carries `vpn: {ip, country, city, status, last_change_at, changes_since_start, last_poll_ok, last_poll_at}` for *this* host. A reconnect (`last_change_at`, `changes_since_start`) is a public IP that changed between two good polls, or a good poll after a failed one; the first poll after start is the baseline. A failed poll keeps the last known exit and sets `last_poll_ok: false`. In memory only, never synced, no alerts

### Writes

Writes take a JSON object body and run only on the ACTIVE node: a passive node answers every `/api` POST with `409 {"error": "passive", "detail", "active": <peer url>}`. Every write accepts `dry_run: true` (same resolution, nothing written, the response says what would happen). `chat_id` defaults to `DEVELOPER_CHAT_ID` and may be a numeric id or `@username`; a numeric id the bot has never seen (no user row, no request, no ban, not a subscriber of anything) is refused with `404 {"error": "unknown chat", "candidates": [...]}` (the closest known ids) unless `force: true`. `{id}` in a user route is a numeric id or `@username` (`404 unknown user` with candidates, `409 ambiguous user`). Ban, unban and unsubscribe post one audit line to the developer chat (`API: banned …`); subscribe and send do not. Takeover/handback and removing a subscription for all its subscribers stay dashboard-only.

- `POST /api/users/{id}/ban`, `POST /api/users/{id}/unban` — `{action, user, changed}`; `changed: false` when the user already was in that state
- `POST /api/subscriptions {url, chat_id?, populate?, force?}` — the same resolution as `/subscribe`: a channel URL subscribes to its Videos feed, a feed or playlist URL of that channel to that entry, a URL yt-dlp cannot place on a channel as is; a video URL gets `409 {"error": "ambiguous url", "playlists": [...]}`. Response `{action: subscribe|add_subscriber, url, subscription, populate: started|skipped}` (`url` is the stored key, which may differ from the request) or `{status: "already_subscribed", …}`; `populate: true` queues the feed's latest videos for that chat; `502 extraction failed` when yt-dlp fails
- `POST /api/subscriptions/remove {url, chat_id?, force?}` — removes one chat; `{action, url, chat_id, removed_subscription}` (`true` when that was the last subscriber); `404 not subscribed` / `404 chat not subscribed`
- `POST /api/intents {url, chat_id?, force?}` — queue a download for a chat through the intent system (dedup with every other requester of the raw URL, `source: "api"`): `{action: "queue", url, chat_id, cached, intent}`; `403 chat banned`. When the URL's intent is *ignored* (a dead video: removed, private, age-gated) the call answers `409 {"error": "intent ignored", "detail", "intent"}` instead of silently appending to it; `force: true` revives the intent like the dashboard's Ignored → Retry and answers `{action: "retry", …}`
- `POST /api/populate {url?}` — run the subscription poll now, for every subscription or one stored key: `{status: started|already_running, scope}`

```bash
curl -H "Authorization: Bearer $ADMIN_TOKEN" http://localhost:8080/api/status
curl -H "Authorization: Bearer $ADMIN_TOKEN" -H "Content-Type: application/json" \
     -d '{"url": "https://www.youtube.com/watch?v=...", "dry_run": true}' http://localhost:8080/api/intents
```

## CLI

`dasovbot` is a stdlib-only client for the admin API (`dasovbot/cli.py`): it knows both HA nodes, probes `/health`, sends writes to the active node and prints JSON (one line; `--pretty` indents). Installed on its own, without the bot's dependencies:

```bash
pipx install -e .          # from the repo root; Python 3.11+ (tomllib)
dasovbot --help
```

Configuration, env first then file (per key): `DASOVBOT_URLS` (comma-separated) and `DASOVBOT_ADMIN_TOKEN`, or `~/.config/dasovbot/config.toml`:

```toml
urls = ["http://192.168.1.10:8080", "http://192.168.1.20:8080"]
admin_token = "..."
```

The admin token is the node's `ADMIN_TOKEN`, or the generated value in `data/admin_token.txt` (stable across restarts).

| Command | Does |
|---|---|
| `status` | one block per node: `{url, reachable, status}` with that node's `/api/status` (a passive node answers too); exit 0 if any node answered |
| `users [--search X] [--banned true\|false]`, `subscriptions [--user ID\|@user]`, `intents [--ignored true\|false] [--url URL]`, `alerts`, `playlists URL` | the read routes, from the first node that answers (active preferred) |
| `ban ID\|@user`, `unban ID\|@user` | `POST /api/users/{id}/ban\|unban` |
| `subscribe URL [--user ID\|@user] [--populate]`, `unsubscribe URL [--user ID\|@user]` | `POST /api/subscriptions`, `POST /api/subscriptions/remove` |
| `send URL [--to ID\|@user] [--wait [SECONDS]]` | `POST /api/intents`; `--wait` polls `GET /api/intents?url=` every 5 s (default 600 s) and prints `{url, result: delivered\|failed\|ignored\|timeout, polls, intent, queued}`; exit 0 only for `delivered` (`failed` = the worker dropped the intent). An ignored intent is refused (exit 1, `intent ignored`) unless `--force`, which retries it |
| `populate [URL]` | `POST /api/populate` |

Every write takes `--dry-run` and `--force`. Writes probe every node, go to the active one and retry once on a `409 passive` (the lease moved between the probe and the request). Exit codes: `0` ok, `1` the server refused (its `{error, detail}` on stderr), `2` no node / no active node answered, `3` not configured.

## Claude Code skill

`skills/dasovbot/SKILL.md` teaches an agent the CLI (its command reference is generated from `dasovbot --help`) and the operator playbooks: spam/abuse → ban, undo a ban, a subscription stopped delivering, a stuck download, "send me this video", subscribe to a channel, health/VPN. Link it into Claude Code once per machine, and refresh the embedded reference whenever a flag changes (`tests/test_skill_doc.py` fails until you do):

```bash
ln -s "$(pwd)/skills/dasovbot" ~/.claude/skills/dasovbot
python -c "from dasovbot.cli import render_help; print(render_help())"   # paste between the cli-help markers
```
