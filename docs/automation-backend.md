# Instagram automation backend

## Integration contract

All routes are under `/api/automation`. The backend registers the router and manages its queue during the existing FastAPI lifespan. No timer performs Instagram polling. The UI may poll the local GET endpoint; that endpoint never contacts Instagram.

- `GET /api/automation`: `{subscriptions, paused, pause_reason, runs, cookie_check}`. Subscriptions contain `id, username, enabled, refresh_enabled, refresh_limit, history_enabled, history_limit, history_status, last_result`. Runs contain `id, username, mode, status, checked, completed, skipped, failed, reason, created_at` (reason can be absent on queued runs). The latest 200 runs are returned; older runs remain in SQLite.
- `POST /subscriptions`: `{username, enabled?, refresh_enabled?, refresh_limit?, history_enabled?, history_limit?}`. Defaults are enabled, refresh enabled/10, history disabled/10. Username accepts `@username`, username, or an Instagram profile URL, normalizes lowercase, and is unique. Posting an existing username updates that subscription.
- `PATCH /subscriptions/{id}`: same partial fields. Username is immutable except equivalent normalized spelling. Null fields and invalid limits return 422; unknown IDs return 404.
- `DELETE /subscriptions/{id}`: cancels active/queued runs and deletes configuration only. Does not remove files, archive, pending posts, history cursor, or run history.
- `POST /trigger`: `{mode: "refresh" | "history", subscription_id?: string}` → `{accepted: int, skipped: int, paused: bool}`. **Accepted means queued, not downloaded.** Disabled subscriptions/modes, already-active same-user/same-mode runs and exhausted history without pending retries are skipped. Both modes can be enabled for one user; they share durable media/post state. Runs execute serially.
- `POST /pause`: `{reason?: string}` persists a global automation pause and stops the current automation run, retaining finalized files. Queued runs remain queued.
- `POST /resume`: `{}` clears the pause and drains queued work. Saving cookies does not resume automation.
- `POST /runs/{id}/cancel`: `{}` cancels queued/running work; already-terminal → 422, missing → 404.
- `POST /cookies/check`: `{username?: string}` → `{login_status, posts_status, checked_at, reason}`. Local-format checks first; explicit network check only when a session is present. Optional username enables a separate bounded posts-access query, not merely identity inference. Cooldown → 429 with Retry-After. Identity states include unknown/authenticated/login_required/error; posts states include unknown/accessible/restricted/error. Failed/ambiguous checks do not assert that cookies are invalid. Cookie replacement invalidates cached results, not the cooldown.
- `POST /subscriptions/{id}/reposition`: `{}` → `{reset: true}`. Explicitly resets only this user's history cursor to the profile head (`null`) and status to `ready`; does not start a run or change subscription settings, post/media archive, pending retries, run history, or files. Unknown subscription → 404; extra payload fields → 422; shared Instagram lock held or any queued/running run for this user → 409. Next history run scans from the head within normal budgets and skips archived posts.
- `POST /subscriptions/{id}/redownload`: `{post_id: string}` resets only that post's success records and marks it pending. It does **not** start a run. 409 while Instagram work is active or queued for this user. Existing files are not deleted; explicit redownload writes media-ID `_redownloadN` filenames. Unknown post → 404. Post IDs are Instagram numeric `pk` when available.

No automation API response contains raw extraction payloads, cookie values, or host paths. Cookie values enter the isolated worker through stdin, never argv. Worker stderr is discarded and exception details are reduced to fixed reason codes. Existing cookie import/export/settings and manual jobs remain supported.

CLI:

```sh
python -m gallery_dl_web.automation trigger refresh
python -m gallery_dl_web.automation trigger history --subscription-id ID
```

The CLI sends an HTTP request to `127.0.0.1:8000`. Exit 0 means the request was accepted/handled (possibly all subscriptions skipped), not successful downloads. Exit 2 means globally paused; exit 1 means HTTP/connection/response failure. There is no built-in cron installer.

## Persistence and files

New database: `DATA_DIR/automation-v1/state.sqlite`, SQLite WAL. It stores subscriptions, runs, pause state, cookie-check results/cooldown, current-page-start history cursors, posts, and independently successful media IDs. It neither imports nor deletes legacy archives or downloads. Therefore old manual downloads are **not** automatically considered archived by automation.

Layout: `DOWNLOADS_DIR/instagram/<normalized-username>/<first-nonempty-title-line>_<shortcode-or-id>/<media-id>.<extension>`. Folder names are sanitized and capped at 180 UTF-8 bytes, identifiers remain suffixes, and a post's first assigned folder remains stable even if its caption changes. Unsafe output symlinks stop the round. No stories, avatar, highlights or separate reels feed is traversed; videos present in the posts feed are included.

A media ID is recorded only after gallery-dl's after-finalize hook, not its pre-finalization file event. A completed post requires every expected carousel media ID to have durable success. Partial retry resolves fresh post metadata/CDN URLs and downloads only missing media. Deleting a file or an entire gallery profile does not reset automation success: explicit redownload is required. Originals survive explicit redownload. Crash exactly between file finalization and SQLite commit can cause a retry; this is not an atomic filesystem/database transaction and strict exactly-once network transfer cannot be guaranteed in that window.

Automation reconciles gallery profile metadata after successful, partial, failed and cancelled runs. Nested paths are preserved in gallery file/avatar/thumbnail URLs and thumbnail cache keys. Existing index, profile ZIP and delete traversal work recursively.

## Limits and safety semantics

Settings/environment variables (defaults):

| Setting | Default | Meaning |
|---|---:|---|
| `AUTOMATION_LIMIT_CAP` | 1000 | Maximum allowed refresh/history limit; minimum is 1 |
| `AUTOMATION_MAX_PAGES` | 10 | Maximum profile pages per round |
| `AUTOMATION_MAX_POSTS` | 1000 | Maximum unique checked posts per round, including retries |
| `AUTOMATION_REQUEST_BUDGET` | 100 | All transport sends per run, including bootstrap, metadata and media |
| `AUTOMATION_RUN_TIMEOUT_SECONDS` | 1800 | Whole-worker deadline |
| `AUTOMATION_COOKIE_CHECK_COOLDOWN_SECONDS` | 60 | Persistent manual-check cooldown |

Refresh starts at the current profile head and checks N non-pinned feed entries; pinned entries encountered before that boundary are checked **in addition**, so old pins cannot consume the newest-post budget. It does not stop at the first archived post. The meaning of "newest" follows Instagram's non-pinned profile feed ordering, not a claim to globally sort inaccessible, deleted or reordered content. Counts refer to posts, not carousel files; previously completed posts count as skipped. Instagram can change ordering and availability while a round is in progress.

History targets N **newly completed** posts, not N attempts or files. Previously completed posts do not consume that target. Failed posts remain pending and can be retried even after the feed is exhausted. The page-start cursor is retained when stopping in a page; its prefix is replayed and archived posts are skipped. Only exhausting a page permits advancing to the next page. This adapter captures actual GraphQL page boundaries because gallery-dl 1.32.18 updates its extractor cursor only after yielding all nodes. It never discovers the boundary by fetching another page. History follows profile feed order (including pinned entries); it is not a strict timestamp sort of pins against all historical pages.

Expired/rejected/unparseable cursor queries yield `needs_reposition`, retain the cursor and stop; there is no automatic full-profile rescan. Affected history is skipped on later triggers until an operator deliberately repositions the stored cursor. Use the subscription's explicit **重新定位历史进度** confirmation in the UI, or `POST /subscriptions/{id}/reposition` with `{}`, to reset only the cursor/status to head/ready. This never clears the archive or files and never starts a run: the next history trigger rescans from the head, skips archived posts and remains bounded by normal scan/request/time budgets. Do not delete the database to work around this: that loses success records. Budget stops return `scan_budget`, `request_budget`, or `time_budget`; continue only by another explicit trigger. Empty final pages indicate exhaustion. Best-effort API cursors cannot guarantee preservation of content Instagram removes/reorders between runs.

429, explicit challenge/checkpoint responses, and explicit invalid-session/login_required JSON persist a global automation pause. Pending automation never continues until manual resume, including across restart. Ambiguous redirects stop only the current round with a fixed `redirect_*` reason (`home`, `login`, `challenge`, `external`, `insecure`, `invalid`, `missing_location`, `unsupported`, `loop`, or `limit`); they are not labeled invalid sessions or automatically paused. Only HTTPS, same-origin canonical trailing-slash redirects on the exact same path/query are followed on known Instagram hosts (`instagram.com`, `www.instagram.com`, `i.instagram.com`), with at most three hops. Cross-host redirects (including between known hosts), HTTP downgrades, credential-bearing destinations, query changes, homepage/login/challenge destinations and arbitrary path changes are never sent. Every hop consumes the shared transport request budget. Requests prepares the next request, preserving its standard method/body/cookie handling, without enabling automatic following. Runs and cookie checks can include an optional sanitized `diagnostic` object: fixed endpoint-category `stage`/`target`, HTTP `status`, and fixed `classification`. These fields survive worker/engine/service persistence and are displayed in both UI result panels; no username, ID, hostname, query, fragment, header, cookie, body or raw upstream error is included. The parent service logs only allowlisted stop reasons. The adapter disables upstream retries and intercepts blocked responses before gallery-dl's 429 handling, so upstream sleep/retry cannot create an unbounded round. Request budget also covers setup requests. Streamed media is not JSON-buffered for classification.

Automation, manual cookie checks, and manual Instagram workers share one asyncio mutex; Facebook does not. A SIGSTOPed manual job relinquishes its execution lock with its old concurrency slot and reacquires it before SIGCONT. This is process-local coordination: run a **single backend process/uvicorn worker**, not multiple API replicas sharing the database. Serial subprocesses isolate gallery-dl's global config. Running jobs become interrupted on restart; queued jobs drain on startup unless globally paused. Cancel/shutdown terminate and reap workers with bounded escalation. Run counters are updated at round completion; after forced worker termination the run can retain its last recorded counters, while media successes remain durable.

Cookies imported from Netscape format are stored intact for Instagram domains. gallery-dl initializes/replaces its own csrftoken and can learn a server token; importing csrftoken does **not** guarantee it is the token used by extraction. No cookie check can establish future access or account safety. `authenticated` identity and successful posts query are deliberately separate facts.

## Verification scope

Offline tests exercise the production Engine, real 1.32.18 GraphQL paginator and real DownloadJob with injected HTTP transport; no real Instagram credentials or requests are required. Tests cover archive durability, carousel retry, pinned budgets, expired cursor stopping, request/scan limits, 429/challenge/session classification, pause/restart/duplicate/cancel, cookie cooldown/invalidation/mutex, API validation, CLI exits, subprocess stdin/timeouts, and nested gallery file/thumbnail/ZIP/delete compatibility.

Real Instagram verification is intentionally deferred until after the build, with explicit authorization and user-provided session. Offline transport fixtures are not proof that Instagram currently accepts its upstream queries, that a particular account is unrestricted, or that original-quality media is available.
