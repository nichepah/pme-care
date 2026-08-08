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

The backend is complete and tested for what it covers: **116 tests** against a
real Postgres, `ruff check` clean, migrations verified to apply, reverse and
re-apply, and [CI](.github/workflows/ci.yml) running all of that plus a
container build on every push.

It handles the full working cycle — register, see who is due, schedule, examine,
record an outcome, compute the next due date — with accounts provisionable in
production. Deployment steps are in [DEPLOYING.md](DEPLOYING.md).

**Deliberately not built yet** — known gaps, not oversights:

| Gap | Why it is deferred |
| --- | --- |
| Notifications | Due dates are computed and queryable, but nothing *sends* anything. Needs a decision on channel (e-mail? SMS? plant noticeboard?) and a scheduler. |
| The full DOC-8 examination state machine | The lifecycle is `SCHEDULED → COMPLETED \| CANCELLED`. The richer machine (in-progress, referred, review) needs the spec document. |
| Parameter/EAV examination model | Vitals are flat columns (`bp_systolic`, `height_cm`, …). The target design keeps a parameter catalogue so new measurements do not need a migration. |
| Attachments | `GCS_BUCKET` and the `google-cloud-storage` / `python-multipart` dependencies are wired for it; no endpoint uses them. Needs retention and access-control decisions first. |
| Firebase web sign-in | The backend verifies ID tokens; the *browser* side that obtains one is not wired. In development the token is typed or picked from seeded accounts. `signInWithFirebase` in [`session.js`](frontend/js/session.js) is the seam, and throws rather than pretending. |
| Rate limiting | Firebase verifies tokens, so this is not a credential-stuffing surface, but nothing stops an authenticated client hammering an endpoint. |

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

Then open **<http://localhost:8080>** — the backend serves the interface itself,
so there is nothing else to start and no CORS to configure.

Interactive API docs: <http://localhost:8080/docs> (disabled when `ENV=production`).

### No Docker? (no daemon, or no root to start one)

Postgres 16 is often already installed without the service running. A
user-owned cluster needs no root at all and works with the default `.env`:

```bash
PGBIN=/usr/lib/postgresql/16/bin              # adjust the version if needed
PGDATA=$HOME/.local/share/pme-care/pgdata

$PGBIN/initdb -D "$PGDATA" -U pme --auth=scram-sha-256 --pwfile=<(echo pme)
# The default socket directory /var/run/postgresql needs root, so keep the
# socket inside the cluster instead:
echo "unix_socket_directories = '$PGDATA'" >> "$PGDATA/postgresql.conf"

$PGBIN/pg_ctl -D "$PGDATA" -l "$PGDATA/server.log" start
PGPASSWORD=pme psql -h 127.0.0.1 -U pme -d postgres -c "CREATE DATABASE pme_care"
```

Use real password authentication, not `--auth=trust`. Trust accepts *any*
password, which makes a local cluster more permissive than CI or production and
will happily hide a credential-handling bug — that is exactly how a redacted
password in the test-database URL passed here and failed in CI.

Stop it later with `$PGBIN/pg_ctl -D "$PGDATA" stop`; delete `$PGDATA` to
discard it entirely. Then continue from step 2 above.

## The interface

[`frontend/`](frontend/) is the application, served by the backend at `/`. No
build step — native ES modules, so it is real code organisation with no
toolchain, and deploying it is copying files.

**Each role lands on the thing its job starts with**, and sees nothing else. The
route table in [`js/app.js`](frontend/js/app.js) *is* that decision, in one
readable place:

| Role | Lands on | Can also reach | Deliberately cannot |
| --- | --- | --- | --- |
| **Employee** | **My status** — am I fit, when is my next examination, my history | nothing else | any list, anyone else's record, their own id |
| **Doctor** | **Examinations to do** — worklist → the examination form, with the person's previous readings beside it | — | registering employees, cancelling, accounts, the audit trail |
| **Health Team** | **Who is due** — overdue / due-soon counts, then the list, with *Book today* on each row | Employees (search, register, amend, give a login), Booked (cancel) | recording a clinical decision, accounts, the audit trail |
| **Admin** | **Who is due** — everything the Health Team has | Accounts (create, deactivate), Audit trail | deactivating or deleting their own account |

Two things that shaped it:

- **The Health Team lands on compliance, not on a search box.** Their job is not
  "look someone up", it is "make sure nobody has lapsed" — so the first screen is
  who has, and booking happens inline. Making someone copy an id into a separate
  form is how a worklist stops getting worked.
- **A doctor sees the previous readings beside the form**, not behind a click. A
  fitness decision is a judgement about a trend, and navigating away to find the
  last blood pressure is how the trend gets ignored.

The nav is built from the same table that guards the routes, so a role is never
offered a page it would be refused — but that is tidiness, not security. Every
rule is enforced again by the API, because anything in a browser can be edited.

Employee names and clinical remarks reach the DOM only through `textContent`;
there is no `innerHTML` with interpolated data anywhere in the app.

[`frontend-mvp/`](frontend-mvp/) is the older endpoint-by-endpoint console. It is
kept because it is still the quickest way to poke one endpoint by hand, but it is
a debugging tool — it shows all four roles at once with editable tokens, which no
real user should ever see.

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
Firebase credentials.

**Account creation works in both modes.** [`app/provisioning.py`](backend/app/provisioning.py)
puts one interface in front of two implementations: locally a readable uid is
synthesized and doubles as a bearer token; in production the Firebase Admin SDK
creates the identity with **no password** and the response carries a sign-in
link, so the user chooses their own credential and this service never handles
one. Routes never import `firebase_admin` themselves, which is what lets the
production path be tested without a Firebase project — the suite stubs the SDK's
functions on the real module.

Deleting an account also disables the Firebase identity and revokes its refresh
tokens, so a live session cannot keep minting ID tokens for other services in
the same project.

### The first administrator

Every account-creating endpoint requires an authenticated admin, and a fresh
database has none — a deadlock that would leave a new deployment unusable.
`scripts/bootstrap_admin.py` is the way in, run once by whoever operates the
deployment:

```bash
python -m scripts.bootstrap_admin --email ops@example.com --name "Ops Admin"
```

It refuses once an active admin exists, so it cannot quietly become a second way
in, and it audits the act with a null actor — an operator with database access is
not an authenticated user.

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
| GET | `/health` | public | **Readiness**: touches the database |
| GET | `/health/live` | public | **Liveness**: process only, never the database |
| GET | `/me` | any | Identity; also stamps `last_login_at` and logs a `LOGIN` audit row |
| POST | `/users` | Admin | Provision a staff account |
| GET | `/users` | Admin | Filter by `role`, `is_active` |
| GET · PATCH · DELETE | `/users/{id}` | Admin | Rename, change role, deactivate, soft-delete |
| POST | `/employees` | HT, Admin | 409 on duplicate personal number |
| GET | `/employees` | staff | `q` (name or number), `department`, `plant`, `is_active` |
| GET | `/employees/due` | staff | **The compliance worklist** — who needs booking, most overdue first. `within_days`, `overdue_only`, `department`, `plant` |
| GET | `/employees/{id}` | any | Detail + latest examination; employee self only |
| PATCH | `/employees/{id}` | HT, Admin | Partial update; `is_active=false` retires |
| DELETE | `/employees/{id}` | Admin | Soft delete; 409 while a PME is open |
| GET | `/employees/{id}/examinations` | any | History, newest first; employee self only |
| POST | `/employees/{id}/login` | HT, Admin | Create + link a login; needs an e-mail in production |
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
  two open ones would make "their current status" ambiguous. This is enforced by
  a **partial unique index**, not just an application check — two concurrent
  requests would otherwise both pass the check.
- **A completed examination sets when the next is due**
  ([`app/periodicity.py`](backend/app/periodicity.py)). The doctor's explicit
  recall date wins; otherwise the outcome's validity period, counted from the
  examination date so a late exam does not compound the drift. An `UNFIT`
  outcome sets **no** due date: that is a case to manage, not a booking to make,
  and inventing a routine recall would quietly downgrade a serious finding.
  Intervals are settings, and the computed date is *stored* — changing the
  configuration later must not silently move dates a doctor committed to.
  *(inferred)*
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
    lookups.py      id parsing + unique-violation → 409 translation
    provisioning.py creating/revoking identities: fake and real Firebase
    periodicity.py  when the next examination falls due
    routes/         system, users, employees, examinations, audit
  alembic/versions/ 0001 core identity · 0002 MVP slice · 0003 constraint parity
                    0004 one open examination · 0005 periodicity
  tests/            one module per aggregate, plus schema parity and concurrency
  scripts/          seed_dev.py · bootstrap_admin.py
frontend-mvp/       no-build demo page
infra/              docker-compose for local Postgres · Cloud Run service
.github/workflows/  CI
```

## Deployment

**Full runbook: [DEPLOYING.md](DEPLOYING.md).** Cloud Run + Neon, declared in
[`infra/cloudrun-service.yaml`](infra/cloudrun-service.yaml) so what is deployed
is reviewable in git rather than living in shell history.

The short version: the `Dockerfile` builds a two-stage, non-root image serving
with a single uvicorn worker — Cloud Run handles concurrency, and one worker per
instance keeps memory in the free tier. A small pool with `pool_pre_ping`
survives Neon's auto-suspend, and `maxScale: 4` caps how many pools exist at
once, because each instance holds its own.

Migrations are never run by the serving container: `alembic upgrade head` is an
explicit deploy step. A container that migrates on boot migrates again on every
autoscale event, and during a rollback it would migrate *forward* while you are
trying to go back. Revisions are additive — nullable columns, partial indexes —
so the previous image keeps working against the new schema and a traffic
rollback alone is usually enough.

`ENV=production` disables `/docs` **and** `/openapi.json`; the schema is the part
that enumerates every route and field, so hiding only the UI hides nothing.

## Continuous integration

[`.github/workflows/ci.yml`](.github/workflows/ci.yml) runs on every push and
pull request:

- `ruff check`
- migrations **up, down, then up again** — a migration that only works forwards
  is a deploy that cannot be rolled back, and the suite would never catch it
  because it builds its schema with `create_all`
- the full test suite, including the schema-parity check
- a container build, asserting `app` imports, `alembic` is on `PATH` in the
  runtime stage, and the image runs as `appuser` — the deploy's migration step
  depends on all three, and no test would notice their absence
