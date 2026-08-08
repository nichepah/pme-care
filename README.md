# PME Care

Periodic Medical Examination (PME) tracking for an industrial employer.

The Health Team registers employees and schedules examinations, a Doctor records
the fitness outcome, and each employee can see their own status — and only their
own. Every change is written to an append-only audit trail.

```
Health Team                Doctor                     Employee
-----------                ------                     --------
register employee   ->
schedule PME        ->     complete PME (FIT /
cancel PME                 TEMPORARILY_UNFIT /
                           UNFIT + remarks)     ->    view own status + history
```

## Status

The backend is a complete, tested vertical slice: 67 tests pass against a real
Postgres, `ruff check` is clean, and the migrations round-trip up and down.

**Deliberately not built yet** — these are the known gaps, not oversights:

| Gap | Why it is deferred |
| --- | --- |
| Real Firebase account creation | Needs a Firebase project + invite/e-mail flow. Today `AUTH_FAKE_MODE` synthesizes a uid; the two endpoints that do it refuse to run with the flag off (see [Authentication](#authentication)). |
| The full DOC-8 examination state machine | The MVP lifecycle is `SCHEDULED → COMPLETED \| CANCELLED`. The richer machine (in-progress, referred, review) needs the spec document. |
| Parameter/EAV examination model | Vitals are flat columns (`bp_systolic`, `height_cm`, …). The target design keeps a parameter catalogue so new measurements do not need a migration. |
| Attachments | `GCS_BUCKET` and the `google-cloud-storage` / `python-multipart` dependencies are wired for it; no endpoint uses them. |
| React + MUI frontend | [`frontend-mvp/index.html`](frontend-mvp/index.html) is a no-build demo page, not the real UI. |
| Reminders / due-date scheduling | Nothing computes when the next PME is due. |

> **The design documents this code cites do not exist in the repository.**
> Docstrings reference an SRS and `API_DESIGN.md` / `DATABASE_DESIGN.md` /
> `UI_DESIGN.md`, along with requirement IDs (`EMP-2`, `DOC-6`, `AUD-1`, …).
> Those files are not on disk. The IDs have been kept as intent markers, but
> anything below marked *inferred* was a judgement call made without them.

## Getting started

Requires Python 3.12+ and Docker (or any reachable Postgres 14+).

```bash
# 1. Database
docker compose -f infra/docker-compose.yml up -d db

# 2. Backend
cd backend
python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
cp .env.example .env          # defaults already point at the compose database
.venv/bin/alembic upgrade head
.venv/bin/python -m scripts.seed_dev      # prints one bearer token per role

# 3. Serve
.venv/bin/uvicorn app.main:app --port 8080 --reload
```

Interactive API docs: <http://localhost:8080/docs> (disabled when `ENV=production`).

### No Docker? (no daemon, or no root to start one)

Postgres 16 is often already installed without the service running. A
user-owned cluster needs no root at all and works with the default `.env`:

```bash
PGBIN=/usr/lib/postgresql/16/bin              # adjust the version if needed
PGDATA=$HOME/.local/share/pme-care/pgdata

$PGBIN/initdb -D "$PGDATA" -U pme --auth=trust
# The default socket directory /var/run/postgresql needs root, so keep the
# socket inside the cluster instead:
echo "unix_socket_directories = '$PGDATA'" >> "$PGDATA/postgresql.conf"

$PGBIN/pg_ctl -D "$PGDATA" -l "$PGDATA/server.log" start
psql -h 127.0.0.1 -U pme -d postgres -c "CREATE DATABASE pme_care"
```

Stop it later with `$PGBIN/pg_ctl -D "$PGDATA" stop`; delete `$PGDATA` to
discard it entirely. Then continue from step 2 above.

For the clickable demo UI, serve `frontend-mvp/` on the port that matches
`ALLOWED_ORIGINS`:

```bash
cd frontend-mvp && python3 -m http.server 5173   # then open http://localhost:5173
```

### Tests

```bash
cd backend
.venv/bin/python -m pytest        # needs the database from step 1 to be up
.venv/bin/python -m ruff check .
```

Tests run against real Postgres, not SQLite: the schema uses `CITEXT`, `INET`,
`JSONB`, partial unique indexes and trigram indexes, so a portable-SQL shim
would test something other than what ships. `DATABASE_URL` comes from
`backend/.env` or the environment — an exported value wins, and `conftest.py`
sets no fallback on purpose, so a misconfigured run fails loudly instead of
quietly using the wrong database.

The suite uses the **same server but its own database**, `<name>_test`, created
on first run. That is not cosmetic: the fixtures `TRUNCATE` every table between
tests and drop the schema at the end, so sharing a database with your
development data would delete it on every run. Nothing extra to configure —
only the database name is swapped.

`tests/test_schema_parity.py` migrates a scratch database and compares it to
`app/models.py`. Since the suite builds its schema with `create_all` while
production runs the migrations, anything declared in only one place would
otherwise be a constraint no test can reach. If it fails, one of the two was
changed alone.

## Authentication

Firebase Auth owns identity. A client sends the Firebase ID token as
`Authorization: Bearer <token>`; the backend verifies it against Google's cached
public keys, looks the `users` row up by `firebase_uid`, and injects a
`CurrentUser`. The **role lives in this database**, never in the token, so
revoking access is a local UPDATE rather than a token-claim change.

`AUTH_FAKE_MODE=true` (development and tests only) skips verification and treats
the bearer value *as* the `firebase_uid`, so the whole stack runs offline with no
Firebase credentials. It also enables the two dev-only endpoints that invent a
uid instead of creating a real Firebase account —
`POST /users` and `POST /employees/{id}/login`. Both refuse with `422` when the
flag is off, because an invented uid matches no Firebase account and nobody
could ever sign in with it. Wiring real account creation is the one piece of
production work the API surface still needs.

### Roles

| | Employee | Doctor | Health Team | Admin |
| --- | --- | --- | --- | --- |
| See own record and history | ✅ | — | — | — |
| Look up any employee | — | ✅ | ✅ | ✅ |
| Register / amend employees | — | — | ✅ | ✅ |
| Schedule / cancel examinations | — | — | ✅ | ✅ |
| Record an outcome | — | ✅ | — | — |
| Soft-delete an employee | — | — | — | ✅ |
| Manage accounts, read the audit trail | — | — | — | ✅ |

Authorization is deny-by-default: a route states its roles through
`require_roles(...)`, and object-level scope is re-checked in the route. An
`EMPLOYEE` caller asking for somebody else's record gets **404, not 403** — a 403
would confirm the other record exists.

## API

All paths are prefixed with `/api/v1`.

| Method | Path | Roles | Notes |
| --- | --- | --- | --- |
| GET | `/health` | public | Liveness + database reachability |
| GET | `/me` | any | Identity; also stamps `last_login_at` and logs a `LOGIN` audit row |
| POST | `/users` | Admin | Provision a staff account (dev-mode only) |
| GET | `/users` | Admin | Filter by `role`, `is_active` |
| GET · PATCH · DELETE | `/users/{id}` | Admin | Rename, change role, deactivate, soft-delete |
| POST | `/employees` | HT, Admin | 409 on duplicate personal number |
| GET | `/employees` | staff | `q` (name or number), `department`, `plant`, `is_active` |
| GET | `/employees/{id}` | any | Detail + latest examination; employee self only |
| PATCH | `/employees/{id}` | HT, Admin | Partial update; `is_active=false` retires |
| DELETE | `/employees/{id}` | Admin | Soft delete; 409 while a PME is open |
| GET | `/employees/{id}/examinations` | any | History, newest first; employee self only |
| POST | `/employees/{id}/login` | HT, Admin | Create + link a login (dev-mode only) |
| POST | `/examinations` | HT, Admin | 409 if one is already open |
| GET | `/examinations` | staff | `status`, `employee_id`, `doctor_user_id`, `scheduled_from`, `scheduled_to` |
| GET | `/examinations/{id}` | any | Employee self only |
| POST | `/examinations/{id}/complete` | Doctor | Records the fitness decision |
| POST | `/examinations/{id}/cancel` | HT, Admin | Reason mandatory |
| GET | `/audit-logs` | Admin | `entity_type`, `entity_id`, `actor_user_id`, `action`, `since`, `until` |

### Conventions

**Lists** are paginated: `?page=1&size=20` (max 100), returning
`{"items": [...], "total": N, "page": 1, "size": 20}`. `total` counts every
match, not just the page.

**Errors** always use one envelope:

```json
{"error": {"code": "BUSINESS_RULE_VIOLATION",
           "message": "Remarks are required when the fitness decision is UNFIT.",
           "details": [{"field": "remarks", "issue": "required"}],
           "request_id": "req_1a2b3c4d"}}
```

`400 VALIDATION_ERROR` · `401 UNAUTHENTICATED` · `403 FORBIDDEN` ·
`404 NOT_FOUND` · `409 CONFLICT` · `422 BUSINESS_RULE_VIOLATION` ·
`500 INTERNAL_ERROR`. The `request_id` is echoed in the `X-Request-ID` response
header and in the access log, so a user-reported error maps to one log line.

A malformed id and an id that does not exist both return 404 — a typo in a URL
can never surface as a 500, and nothing is leaked about which ids are real.

## Business rules

Rules marked *inferred* were decided without the spec documents. They are the
places to check first if the real requirements say otherwise.

- One open examination per employee. A second `SCHEDULED` PME is a 409, because
  two open ones would make "their current status" ambiguous.
- `COMPLETED` and `CANCELLED` are terminal. A finished PME is never reopened; a
  new one is scheduled instead, so the record of what was decided when stays
  immutable. *(inferred)*
- Cancelling needs a reason, and frees the employee to be rescheduled.
  *(inferred)*
- **Any outcome other than `FIT` requires remarks.** The original code required
  them for `UNFIT` only; `TEMPORARILY_UNFIT` has the same consequences for the
  employee and the same need for justification. *(inferred — widened)*
- No examination for a retired (`is_active=false`) employee. *(inferred)*
- Deleting an employee is refused while a PME is open — cancel it first, so an
  examination is never orphaned — and deactivates their login, so the person
  cannot still authenticate afterwards. *(inferred)*
- An Admin cannot deactivate, demote or delete their own account. That is how a
  deployment ends up with nobody able to administer it. *(inferred)*
- Personal numbers and e-mail addresses are unique among **live** rows only, via
  partial unique indexes, so a soft-deleted record never blocks re-registering
  the same person.

## Data and audit

Nothing is ever hard-deleted: `deleted_at` / `deleted_by` are set and every
query filters `deleted_at IS NULL`. Medical history and its audit trail have to
outlive the record they describe.

`audit_logs` is append-only and has no foreign keys, so rows survive whatever
they point at. Its `summary` carries **changed field names and technical
metadata only — never clinical values**; `tests/test_audit.py` asserts that a
blood-pressure reading and a diagnosis do not appear in the trail. Client IP is
captured from `X-Forwarded-For` (Cloud Run's real-caller header), and anything
that is not a valid IP literal is stored as NULL rather than rejected by the
`INET` column.

## Layout

```
backend/
  app/
    main.py         middleware (request id, security headers, access log),
                    error handlers, router wiring
    config.py       env-driven settings
    db.py           engine + request-scoped session
    auth.py         token verification, CurrentUser, require_roles, error types
    context.py      per-request ambient values (request id, client IP)
    models.py       the whole schema, constraints and indexes included
    schemas.py      request/response models
    paging.py       the shared pagination envelope
    lookups.py      id parsing + the dev-mode account shim
    routes/         system, users, employees, examinations, audit
  alembic/versions/ 0001 core identity · 0002 MVP slice · 0003 constraint parity
  tests/            one module per aggregate, plus schema parity
  scripts/seed_dev.py
frontend-mvp/       no-build demo page
infra/              docker-compose for local Postgres (+ optional API container)
```

## Deployment

Targets Cloud Run + Neon. The `Dockerfile` builds a two-stage, non-root image
that serves with a single uvicorn worker — Cloud Run handles concurrency, and one
worker per instance keeps memory inside the free tier. A small connection pool
with `pool_pre_ping` survives Neon's free-tier auto-suspend.

Migrations are never run by the serving container: `alembic upgrade head` is an
explicit deploy step, so a restart can never silently alter the schema.

Before going to production: set `ENV=production` (which disables `/docs` **and**
`/openapi.json` — the schema is the part that enumerates every route and field),
set `AUTH_FAKE_MODE=false`, set `ALLOWED_ORIGINS` to the real frontend origin,
provide `FIREBASE_PROJECT_ID` with credentials available to the Admin SDK, and
implement real Firebase account creation for the two dev-only endpoints.
