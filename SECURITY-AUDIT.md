# Security Audit — On Track (life-tracker)

**Date:** 2026-08-03
**Target:** `https://life-tracker.tomkeefe.ai` (Railway, single service + Postgres plugin)
**Method:** Five parallel single-lens audits (auth/session, secrets/exposure, web+API surface,
infrastructure/data-at-rest, live black-box probe). Findings verified against the running system
where verification was possible; inferences are marked.

---

## Verdict

**Yes — this is safe to leave on a public hostname right now.** There are no critical findings: a
stranger who finds the URL hits a password wall with a working lockout, no data route answers
without a session, the database is not publicly reachable, and no route can spend money on the
Anthropic API. The real gaps are all in the "what happens after something else goes wrong"
category — backup recoverability, alerting, and revocation — plus one unauthenticated
denial-of-service vector.

---

## Critical (fix before it stays public)

**None.** This section is deliberately empty rather than padded. The findings below are real and
worth fixing, but none of them lets an unauthenticated stranger read data, spend money, or take
over the app.

---

## Important (fix this week)

### 1. No request body size cap — unauthenticated memory-exhaustion DoS
`Procfile:1`, `app/api.py:136-137,157`

`POST /api/login` is reachable with no auth. The `password` field is capped at 200 characters, but
Pydantic enforces that only *after* Starlette has read and parsed the entire request body into
memory. Confirmed live: a 1 MB body was accepted onto the wire and rejected at field validation —
no `413`, no transport-layer cap observed from outside. Repeated concurrent multi-hundred-megabyte
POSTs would exhaust the single service's memory. The login lockout does not help, because the body
is fully buffered before the lockout logic runs.

*Reachable unauthenticated right now: yes.* Impact is availability only — no data exposure.

**Fix:** ASGI middleware rejecting requests whose `Content-Length` exceeds a small ceiling (16–64 KB
covers every JSON body this app accepts) before the body is read.

### 2. Backup bucket ACL and key scope are unverified, dumps are unencrypted, restore is untested
`jobs/backup_db.py`

The backup job itself is well built — credentials passed via environment rather than argv, a
minimum-size guard, 30-dump retention that prunes only after verifying the new upload landed, and
`safe_status()` on the failure path. But three things are open:

- The `pg_dump` output is uploaded to Backblaze B2 **unencrypted**, protected only by TLS in transit
  and the bucket's own ACL.
- Whether that bucket is private, and whether `BACKUP_S3_ACCESS_KEY` is scoped to just that bucket
  versus account-wide, **could not be verified** from the repo or Railway — it needs a look in the
  Backblaze console.
- There is **no restore script and no documented restore path**. A `pg_restore` from a B2-hosted
  dump has never been tested.

This matters more than it would elsewhere because the database is the highest-value object in the
system (see Blast Radius below) and because SimpleFIN's 90-day rolling window means a backup gap is
**irreversible** data loss, not delayed data.

**Fix:** confirm the bucket is private and the key is bucket-scoped write+list; GPG-encrypt the dump
before upload; write and actually run a restore once.

### 3. Nothing alerts on the failure signals the code already computes
`app/api.py:123`, `jobs/backup_db.py`, `services/telegram_notify.py`

Failed logins are logged and lockouts are recorded. Backup status is written to `app_settings`. But
nothing pushes a notification anywhere — the only way to learn that someone is hammering the login
or that backups have been failing for a month is to manually open Railway's logs (short retention)
or the Settings screen. There is also no spend monitoring on the Anthropic account (unverifiable
from here — check the console).

**Fix:** wire the existing send-only `telegram_notify` channel to fire on (a) lockout threshold
crossed and (b) `backup_last_status` transitioning to anything other than `ok`. This is wiring, not
new infrastructure.

### 4. `starlette 0.52.1` running in production with known CVEs
`requirements.txt` (transitive via `fastapi==0.128.8`, unpinned)

Confirmed from the actual Railway build logs, not just a local resolve — production installs
`starlette-0.52.1`. Seven advisories apply. Reachability against *this* app's code was checked
individually and is low: the `Host`-header path-desync issue (`PYSEC-2026-161`/`-248`) only matters
where app code makes security decisions from `request.url.path`, and the single place this app reads
it is a Cache-Control header choice (`app/api.py:148`) — `require_auth` never touches it. The
unbounded form-body issue needs `Form(...)`/`request.form()`, which appears nowhere. The Windows
SSRF and `HTTPEndpoint` issues don't apply.

So: low impact today, but it is an outdated unpinned dependency, and the path-desync CVE becomes a
real bypass the moment anyone adds path-based logic.

**Fix:** pin `starlette>=1.3.1` in `requirements.txt`.

*Note:* the local venv has `starlette==1.2.0` — already patched. That drift is why testing locally
would not have surfaced this.

### 5. `railway variables *` is auto-approved for Claude Code sessions
`.claude/settings.local.json:64`

That command prints `ANTHROPIC_API_KEY`, `APP_PASSWORD`, `SIMPLEFIN_ACCESS_URL`,
`GOOGLE_CALENDAR_REFRESH_TOKEN`, and `DATABASE_URL` to stdout with no confirmation prompt, where
they land in a session transcript. This partly defeats the redaction boundary built carefully
everywhere else in the codebase — the SimpleFIN URL in particular *is* the credential.

Not a remote-attacker path. It is a local tooling path, and this very audit exercised it: the
infrastructure auditor read the variable list under that approval. Values were masked in its report
and **nothing leaked into the session transcript**, so no rotation is needed — but that was
instruction-dependent, not enforced.

**Fix:** remove `Bash(railway variables *)` from the allow list. The other Railway entries
(`status`, `deployment *`, `list_deployments`) don't print secret values and can stay.

### 6. No way to revoke sessions
`app/auth.py:56-60`, `database.py`

`database.py` exposes only `delete_session(token)` and an expiry sweep. There is no
`delete_all_sessions()` and no route that calls one. Because sessions are deliberately decoupled
from the password (which is the right design — it's why a leaked password can't be used to forge a
cookie), **rotating `APP_PASSWORD` does not invalidate existing cookies.** If a cookie leaks, the
only remediation is waiting out the 60-day cap or deleting rows from Postgres by hand.

**Fix:** a `delete_all_sessions()` and a "sign out everywhere" button.

---

## Minor (worth doing eventually)

- **Bank identifiers committed to git.** `scripts/simplefin_backfill.py:40-49` hardcodes bank names
  and account last-4 digits. Repo is confirmed private, so nothing is exposed — but it's in history,
  which makes it a **pre-public blocker requiring a history rewrite**, not a line deletion.
- **Unbounded user text.** `LabelPatch.label` (`app/routes.py:452-456`) and the `nickname` handler
  (`app/routes.py:375-386`) have no length cap, unlike every other user-text field. Authenticated
  self-harm only.
- **`Cache-Control` absent rather than explicit on `/api/*`** (`app/api.py:28-49`). Relies on browser
  defaults not to cache financial data. Set `no-store, private` explicitly.
- **v1 archive tables are pure blast radius.** `people` and `life_log_entries` hold named individuals
  with relationship notes and are read by no code path. Export and drop them.
- **`DATABASE_PUBLIC_URL` is a stale orphaned template** on the Postgres service. Harmless today
  (resolves to an empty host), but it means one dashboard toggle produces a working public
  connection string with the password already in place.
- **CSP has no `script-src`** — only `frame-ancestors`. No XSS sink exists today to exploit the gap,
  but it's free defense-in-depth.
- **HSTS lacks `preload`**; no `Permissions-Policy`.
- **`SESSION_TTL_DAYS` / `SESSION_MAX_DAYS` are unset** in Railway — running on code defaults (14/60)
  by accident rather than decision.
- **No minimum enforced on `APP_PASSWORD`.** Moot in practice: the live value is 32 characters, mixed
  case and digits. Worth a startup sanity check anyway.
- **`inventory.tomkeefe.ai`** appears in Certificate Transparency alongside this app. Not probed —
  a separate personal app that deserves its own audit.

---

## Before a public / multi-user version

These are not bugs. They are the things that silently become vulnerabilities the moment there is a
second user, and they are far cheaper to decide now than later.

1. **There is no `user_id` anywhere** — not on `sessions`, not on any data table. `require_auth`
   proves "a valid session exists," never "which user." Every query returns all rows, which is
   correct today because all rows are yours. Adding users without redesigning this produces a
   "user B sees user A's bank data" hole across every query in `database.py`. This is a data-layer
   rebuild, not a patch.
2. **Per-user credentials break the current secrets design.** `SIMPLEFIN_ACCESS_URL` and
   `GOOGLE_CALENDAR_REFRESH_TOKEN` are process-wide env vars. Multi-user requires them stored
   per-user and encrypted at rest — which changes the redaction boundary's core assumption that the
   credential never leaves `config.py` and `services/simplefin_service.py`.
3. **No application-layer field encryption.** Bank data, `user_note` free text, and substance
   check-ins are plaintext in Postgres, protected only by Railway's disk encryption (vendor claim,
   not independently verified). Acceptable for one user; not for many.
4. **Logs and `app_settings` are single-tenant by assumption.**
5. **Scrub `ROLE_SEEDS` and rewrite history** before any repo visibility change.

---

## Blast radius if the database leaks

Worth stating plainly, because it's what makes the backup findings matter: `bank_transactions` and
`bank_accounts` give a complete financial picture; `checkins` gives dated alcohol and substance use
— including the one metric the app itself marks `private: True`, a privacy control enforced only on
two output paths and not at the storage layer; `calendar_events` gives titles, locations, and which
events were dates; `rides` and `delivery_orders` give a behavioral and location-adjacent pattern;
`weekly_reflections` holds AI-written summaries of the person's habits, arguably more sensitive per
byte than the rows they came from; and the unused v1 `people` table holds named individuals with
relationship notes.

---

## Checked and clean

**Verified live against the running app:**
- HTTP → HTTPS redirect; valid Let's Encrypt cert (expires 2026-11-01).
- All 15 probed data routes return a uniform `401` with no cookie. Nothing returned data.
- `/docs`, `/redoc`, `/openapi.json` all `404` — no route map exposed.
- Brute-force lockout works: attempts 1–5 → `401`, attempts 6–8 → `429`. Matches
  `LOCKOUT_THRESHOLD = 5`.
- Security headers present: HSTS (1 yr, `includeSubDomains`), `frame-ancestors 'none'`,
  `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy: same-origin`.
- Unknown `/api/*` paths return real JSON `404`s — no SPA catch-all masking.
- No `.git/config`, no `.env`, no `/assets/` directory listing, no shipped source map.
- Origin not reachable directly by IP.

**Verified live against the Railway platform:**
- **No public TCP proxy on Postgres**, no domains on the database service, app connects via
  `postgres.railway.internal`. Confirmed three independent ways.
- Only two services exist; no stale preview environments.
- `APP_PASSWORD` is 32 chars, mixed case and digits — not guessable.
- Start command has no `--reload`; binds `0.0.0.0` and reads `$PORT`.

**Verified by source reading:**
- Password compared with `hmac.compare_digest`. Session token is a 256-bit random value stored
  server-side, not derived from the password, not forgeable, individually revocable.
- `SESSION_MAX_DAYS` cap enforced on every request from an immutable `created_at`, so sliding
  renewal cannot extend a session past it. Checked specifically for an off-by-one; there isn't one.
- Cookie flags: `HttpOnly`, `Secure` (unconditional), `SameSite=Lax`, host-scoped.
- Fail-closed on missing `APP_PASSWORD` — the process refuses to start.
- All 30 routes gated by one router-level dependency (`app/routes.py:18`) with no per-route override.
- **No route can trigger an Anthropic call except `/api/reflection`**, which computes its week
  server-side and checks the cache first — at most one Claude call per week, and zero for a stranger.
- All SQL parameterized; the one dynamic column list is whitelisted (`database.py:1151-1164`).
- No `CORSMiddleware`, no wildcard origin.
- No `dangerouslySetInnerHTML`, `innerHTML`, `eval`, or URL sinks in the frontend — matters because
  much stored text originates from third parties who email or invite you.
- No SSRF: every outbound URL is config-derived.
- Prompt injection from emails/invites is contained — outputs are read back as narrowly-typed
  coerced fields, and malformed responses no-op to a default.
- The redaction boundary holds: `safe_status()` never touches exception text, httpx/httpcore pinned
  to `WARNING` with an explicit level, the Google stack logs URLs only at DEBUG, and `pg_dump`
  credentials never reach argv.
- `user_note` never reaches any AI — pinned by a real sentinel test.
- Private metrics stripped from both the Telegram push and `/api/reflection`.
- No secrets in the working tree or anywhere in git history.
- No `VITE_*` usage — nothing secret in the client bundle.
- `scripts/cleardb.py` unreachable from any route or job; requires typing the exact database
  hostname on Postgres.
- No `DROP` or `TRUNCATE` anywhere; startup migrations are idempotent and additive.
- `npm audit --production`: 0 vulnerabilities across 6 production dependencies; lockfile committed.

---

## Not applicable

- **IDOR / per-user row filtering** — single-user app; there is no other user's data to reach. Listed
  under the multi-user section instead.
- **File upload validation** — no upload endpoint exists.
- **`eval`/`exec`/shell injection** — the only `subprocess` call is `pg_dump` with a fixed argv list,
  scheduler-only, no user input.
- **CSRF tokens** — every state-changing route is POST/PATCH/DELETE and `SameSite=Lax` withholds the
  cookie cross-site. No state-changing GET exists.

---

## Disclosure — what this audit did to the live system

- Sent 8 wrong-password logins, which **triggered a real lockout**. It self-cleared in about 60
  seconds.
- Sent one `POST /api/reflection` with no cookie to confirm POST routes are gated. It returned `401`
  before the handler ran; nothing was created or modified.
- All other probes were `GET`. No data was written, modified, or deleted.
