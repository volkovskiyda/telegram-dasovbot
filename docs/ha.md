# High availability

Two nodes run the same image, a `primary` (the Proxmox LXC) and a `standby` (the Raspberry Pi), and the bot survives the primary host going down. Both processes stay up all the time; a **role controller** (`services/ha.py`) decides which one talks to Telegram. HA is off unless both `PEER_URL` and `SYNC_SECRET` are set; without them a node is standalone and behaves exactly as a single deployment.

- [Roles and lease](#roles-and-lease)
- [Handoff point](#handoff-point)
- [Data sync](#data-sync)
- [Endpoints](#endpoints)
- [Syncthing](#syncthing)
- [Backups](#backups)
- [Addressing](#addressing)
- [Rollout](#rollout)

## Roles and lease

```
            lease lost / cold start /                 handoff requested          peer reports
            handoff drained                           by the peer                ACTIVE
  PASSIVE ─────────────────────────────▶ ACTIVE ─────────────────────▶ DRAINING ─────────────▶ PASSIVE
     ▲                                     │
     └──── 409 Conflict (standby only) ────┘
```

- **ACTIVE** polls Telegram and runs every background task (subscriptions, intent worker, inline cache cleanup, media sweep, backup and disk monitoring, tombstone pruning). **PASSIVE** runs only the dashboard (read-only), `/health`, `/sync/*` and the sync client. **DRAINING** has stopped polling and every background task; the download or upload that was in flight is abandoned (its files are removed) and the intent is retried by the node that takes over.
- Every node heartbeats the other every `HEARTBEAT_INTERVAL_SEC` (`GET /sync/heartbeat`); the passive one pulls the change feed after each reply. **Lease lost** = no reply saying ACTIVE or DRAINING for `LEASE_TTL_SEC` (a peer that answers PASSIVE counts as lost too; that is how a stepped-down standby is relieved). The standby then becomes ACTIVE if it is **ready**: its last sync is within 3 × `LEASE_TTL_SEC` of the last heartbeat it got, whatever role that heartbeat reported. Otherwise it stays passive, alerts the developer and keeps trying.
- **Cold start** with no active peer in sight: the primary claims after `LEASE_TTL_SEC`, the standby after `LEASE_TTL_SEC + HEARTBEAT_INTERVAL_SEC`, so a simultaneous cold start goes to the primary. One missed probe is deliberately not enough: a standby that had been ACTIVE for hours answers the second or third probe, and a primary that activated after the first used to strand everything the standby had written (see [Handoff point](#handoff-point)).
- **Failback:** the returning primary starts PASSIVE, syncs, and after `FAILBACK_STABLE_SEC` of continuous healthy heartbeats requests the lease back (`POST /sync/handoff`). The standby stops polling and cancels every background task at once (nothing waits for the download in progress), answers `drained: true`, the primary pulls the final changes and becomes ACTIVE; the standby goes PASSIVE when it sees that. A drained node whose peer never activates within `LEASE_TTL_SEC` re-activates itself: nothing is ever left with nobody polling. The requesting node then treats its handoff as lapsed and asks again after `LEASE_TTL_SEC`. On activation a node first deletes the unfinished downloads (`*.part`, `*.ytdl`, fragments, temp files) left in its media folder.
- **Split-brain breakers:** a standby that is ACTIVE and sees the primary ACTIVE in a heartbeat steps down; a standby that gets Telegram's `409 Conflict` steps down at once (the primary logs it and keeps polling). Telegram's 409 only fires between pollers of the *same* Bot API server, so the heartbeat is the real guard: if the LAN between the hosts fails while both are up, both will poll until it heals (accepted). **Hold-down:** after a step-down the node refuses the next automatic *lease lost* takeover for 2 × `LEASE_TTL_SEC`, doubling per consecutive step-down up to 15 min, so a flapping link costs one short split-brain window per hold-down instead of one per TTL; `FAILBACK_STABLE_SEC` of uninterrupted heartbeats clears the backoff. Handoffs and **Take over** are not held down (the peer, or the operator, asked for it).
- **Backlog on activation:** every local Bot API server receives every update and buffers it while nobody polls it, so the first poll after activation returns everything the other node already handled. The activating node drains that backlog first and keeps only messages dated after the peer was last seen ACTIVE (minus one heartbeat interval, on this host's clock against Telegram's timestamps); inline and callback queries in the backlog are stale and dropped. Duplicates are bounded to about one heartbeat interval.
- **Manual override:** `/system` shows a role card with **Take over** (passive node; readiness still enforced) and **Hand back** (active node; on the primary the button reads *Hand over to standby*). A manual takeover of the standby sets a persisted **hold**: automatic failback is suspended until Hand back is clicked on either node.
- **Notifications:** the developer chat gets a message for 🟢 *now ACTIVE* and 🔴 *stepped down*; the intermediate steps of a planned handoff (⏳ requested, 🟡 drained, ⚪ handed over) go to the history only. Errors (⚠️ cannot take over, sync error, could not absorb) are sent at most once per 10 minutes. An identical transition repeated within 10 minutes is not re-sent; the next copy that goes out carries the collapsed count (`+3 collapsed in the last 10 min`). Every transition (collapsed and quiet ones included) and every error notice that was sent is kept per node in `sync_meta` (`ha:events`, last 200) and listed newest-first on `/system` as **HA history**; container logs disappear with the container, this does not.

## Handoff point

Only passive nodes pull, so whenever a node becomes ACTIVE without the peer having pulled its feed first, the rows it then writes exist only on itself. The **handoff point** (`handoff_rev`, persisted in `sync_meta`) tracks that debt:

1. A node that activates records its current revision as its handoff point (an older persisted point is kept: a restart while ACTIVE must not move it) and advertises it in every heartbeat reply.
2. The peer, on seeing a handoff point it has not handled yet, runs a **reconcile** against it: it pulls the node's feed from that point (the node wins on every key it touched, tombstones included, since the two counters are not comparable above the point), and re-stamps its own surviving rows with fresh revisions so they flow the other way on the next regular pull. This happens on the PASSIVE peer (the classic returning-primary case) and also on an ACTIVE peer whose heartbeat shows a PASSIVE node still advertising a point: the standby served alone, the primary came back, claimed the lease and the standby stepped down; the primary now absorbs the standby's writes instead of leaving them to be deleted by the next snapshot floor.
3. The peer reports the point it reconciled as `reconciled_rev` in its heartbeat replies, and the node clears its handoff point when it sees its own value echoed back. A planned handoff clears it directly (the taker pulled the feed to the end before activating); a step-down does **not**, for the reason in 2.

Without step 3 a primary kept its very first activation point for life and every standby start re-walked the whole feed from it.

## Data sync

The database is replicated by the app, **not** by Syncthing. Every write on the active node stamps the row with a revision from a per-node Lamport-style counter (`sync_meta`); deletes write a `tombstones` row. The passive node pulls `GET /sync/changes?since=<rev>` in pages of 500 ordered by revision and applies rows and tombstones into SQLite and memory through `apply_remote_*` paths that never trigger side effects (no downloads, no messages). In a regular pull a tombstone only deletes a local row that is not newer than it; in a reconcile pull (see above) the peer's tombstones win outright.

Once an hour the passive node pulls `GET /sync/snapshot` (a SQLite backup file) and reconciles against it as a self-healing floor: its keyed tables and tombstones become equal to the peer's, `requests` are unioned. A fresh standby bootstraps from the snapshot. A snapshot whose SHA-256 does not match `X-Dasovbot-Sha256`, that fails `PRAGMA quick_check`, or that holds a row whose JSON does not parse is rejected before it touches the local database (`sync error: snapshot rejected: …`), and the next tick pulls it again. The floor also means **a passive node's own rows survive at most an hour** unless the active node has absorbed them through the handoff point; that is what the point exists for.

Both the feed and the snapshot pulls run inside the controller's tick with a 30 s read timeout, so a peer host that loses power mid-response (no RST) stalls the lease check for half a minute, not for the request's total timeout.

`requests` rows merge by append (unique on user, url and second); two identical requests within one second count once. Up to `HEARTBEAT_INTERVAL_SEC` of the primary's last writes may be invisible to the standby at takeover; they come back through the handoff reconcile when the primary returns.

**Housekeeping:** the ACTIVE node prunes tombstones older than 30 days every hour. A peer away for longer misses those deletes in the feed, and the next snapshot reconcile heals it.

**Not synced:** the in-memory inline-query cache (users simply re-query), `animation_file_id` (set `ANIMATION_FILE_ID` on both nodes or each activation re-uploads the animation once), dashboard sessions (log in again on the other node), health alerts, the VPN status, the `/media` working folder. `/export` is replicated by Syncthing; its lag never blocks a takeover.

## Endpoints

`/health` is public (it is the Docker healthcheck and the peer's liveness probe; on a LAN-only port its topology fields are acceptable); `/sync/*` require `Authorization: Bearer <SYNC_SECRET>` (never the API token or a session).

| Endpoint | Role | Request | Response |
|---|---|---|---|
| `GET /health` | any | — | `200 {role, node, node_url, lease_holder, last_sync_rev, last_sync_at, ready, peer, peer_url, node_role, enabled, rev, manual_hold, handback_requested, drained}`; a node without HA reports `role: active`, `ready: true`; `node_url` is this node's own address as the peer reaches it (the peer's `PEER_URL`, from the last heartbeat, `null` until then), `peer`/`peer_url` the other node |
| `GET /sync/heartbeat` | any | — | `200 {node, role, lease_until, rev, drained, manual_hold, handback_requested, handoff_rev, reconciled_rev, peer_url}`; `503` when HA is off. `handoff_rev`/`reconciled_rev` drive the [handoff point](#handoff-point); `peer_url` tells the caller its own address |
| `GET /sync/changes` | any | `?since=<rev>&limit=<n≤500>` | `200 {since, until, has_more, rows: {videos, intents, users, subscriptions, banned_users, requests}, tombstones}`; `400` on bad params |
| `GET /sync/snapshot` | any | — | `200` SQLite file (`application/x-sqlite3`, headers `X-Dasovbot-Rev`, `X-Dasovbot-Sha256`) |
| `POST /sync/handoff` | active | `{"manual": bool, "node": name}` (`node` is informational) | `202 {accepted, role: draining, drained: false}` on ACTIVE, `200` with the current `drained` when already draining, `409 {error}` when passive or when the primary is asked for an automatic handoff |

Force a takeover from a shell (run against the **active** node, then watch `/health` on the other):

```bash
curl -X POST -H "Authorization: Bearer $SYNC_SECRET" -H "Content-Type: application/json" \
     -d '{"manual": true}' http://192.168.1.10:8080/sync/handoff
curl http://192.168.1.20:8080/health
```

## Syncthing

The app never syncs files. `/data` (folder `dasovbot`) and `/export` (folder `Telegram`) are shared between the hosts by Syncthing, so the hot database must be excluded or the passive replica can overwrite the live file. The generated secrets are excluded too: each node generates and keeps its own, and on an HA pair the three secrets must be set explicitly (same value on both nodes) instead.

```
# .stignore for the dasovbot folder (/data) — bot.db.backup_* stays synced
bot.db
bot.db-wal
bot.db-shm
*.db-journal*
dashboard_password.txt
api_token.txt
admin_token.txt
```
```
# .stignore for the Telegram folder (/export): exported videos land as <name>.partial
# and are renamed into place atomically
**/*.partial
```

**Both `.stignore` files must be in place on both Syncthing instances before the standby bot is started for the first time.**

## Backups

Only the active node's cron runs `backup.py`: it asks `http://127.0.0.1:$DASHBOARD_PORT/health` and exits 0 (skipped) when the node is passive or the bot is not answering, so two cron jobs never prune each other's files in the shared `/data`. `BACKUP_SKIP_ROLE_CHECK=true` (env, or `docker exec dasovbot env BACKUP_SKIP_ROLE_CHECK=true python backup.py`) forces a backup of a passive replica. `monitor_backups` alerts only on the active node. The `subscriptions.py` CLI asks the same `/health` and refuses to write unless the node is active (rows written on a passive node are overwritten by the next sync); `--skip-role-check` writes anyway, e.g. while the bot is stopped. Prefer `dasovbot subscribe` ([docs/api.md](api.md#cli)), which goes through the running bot.

## Addressing

Each node keeps its own dashboard address; the passive dashboard shows a banner linking to the active node, and `/health` tells you who is active. There is no floating IP. `PEER_URL` is the peer's **static LAN IP** (`http://192.168.1.10:8080` on the standby, `http://192.168.1.20:8080` on the primary; the Pi has a reserved DHCP lease): both bots run inside gluetun, whose built-in DNS-over-TLS resolver ignores Pi-hole and the router, so LAN hostnames never resolve inside the container. gluetun on both hosts needs `FIREWALL_OUTBOUND_SUBNETS=192.168.1.0/24` so the bot can reach the peer, and each host its own WireGuard key (most providers allow one session per key).

## Rollout

1. Put the `.stignore` files above on both Syncthing instances.
2. On the standby, delete the stale replica (`bot.db`, `bot.db-wal`, `bot.db-shm` under `/data`); the standby bootstraps from the primary's snapshot.
3. Primary: set `NODE_ROLE=primary`, `NODE_NAME`, `PEER_URL=http://192.168.1.20:<port>`, `SYNC_SECRET`, plus explicit `DASHBOARD_PASSWORD`, `API_TOKEN` and `ADMIN_TOKEN`; `docker compose up -d` (`vpn → api → bot`); `curl /health` → `role: active` after `LEASE_TTL_SEC`.
4. Standby: the same with `NODE_ROLE=standby`, `NODE_NAME=rpi`, `PEER_URL=http://192.168.1.10:<port>`, the same `SYNC_SECRET`/`DASHBOARD_PASSWORD`/`API_TOKEN`/`ADMIN_TOKEN`; `docker compose up -d`; watch `/health` go `role: passive`, `ready: true` after the bootstrap snapshot.
5. Rehearse once: Take over on the standby's `/system`, send the bot a link, Hand back, confirm the primary is ACTIVE again and the video shows on both dashboards.

**Rollback:** blank `PEER_URL` on both nodes and restart; each node is standalone again (stop the standby's bot, or both will poll).

**Reading the state:** `curl http://<node>:8080/health` (`role`, `lease_holder`, `ready`, `last_sync_at`) or `dasovbot status --pretty`; the `/system` card shows the same plus the peer's role, heartbeat age and sync revision; the developer chat messages above mark every transition.
