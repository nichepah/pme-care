# Deploying PME Care

Two supported shapes. **Pick one and read only that section.**

| | [On your own server](#a-on-your-own-server) | [Cloud Run + Neon](#b-cloud-run--neon) |
| --- | --- | --- |
| Postgres | local, on the same host | Neon (managed) |
| Serves the UI | yes, same process | yes, same image |
| Data leaves the site | no | yes |
| Cost | hardware you already have | free tier, then usage |
| You are responsible for | backups, updates, uptime | almost nothing |

**A is the current plan.** Everything the two share — Firebase setup, the secret,
the first administrator, the post-deploy checks — is written once under A and
referenced from B.

---

## A. On your own server

One host runs Postgres, the API, and the interface. One port, no CORS, no cloud
account. This is the right shape when the data should not leave the site, which
for occupational health records is a reasonable default.

### 1. Postgres

Use your distribution's package, not a container, so it starts with the machine
and its backups are ordinary filesystem backups:

```bash
sudo apt install postgresql-16
sudo -u postgres createuser --pwprompt pme
sudo -u postgres createdb --owner=pme pme_care
```

Keep it listening on localhost only — the API is the only thing that talks to it.
Confirm with `ss -lntp | grep 5432`: it should show `127.0.0.1:5432`, never
`0.0.0.0`.

Use **password authentication** (`scram-sha-256`), not `trust`. Trust accepts any
password, which makes the machine more permissive than CI and will hide a
credential bug rather than fail on it.

### 2. The application

```bash
git clone https://github.com/nichepah/pme-care /opt/pme-care
cd /opt/pme-care/backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env      # then edit: DATABASE_URL, ENV=production, AUTH_FAKE_MODE=false
.venv/bin/alembic upgrade head
```

`.env` holds the database password, so `chmod 600 .env` and keep it owned by the
service user. It is git-ignored; do not add it.

### 3. Run it as a service

```ini
# /etc/systemd/system/pme-care.service
[Unit]
Description=PME Care
After=network.target postgresql.service
Requires=postgresql.service

[Service]
User=pme-care
WorkingDirectory=/opt/pme-care/backend
EnvironmentFile=/opt/pme-care/backend/.env
# One worker is not a constraint here: this is an IO-bound app and a plant-sized
# workforce is a handful of concurrent users. Raise --workers only with evidence,
# and remember each one holds its own connection pool.
ExecStart=/opt/pme-care/backend/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8080
Restart=on-failure
RestartSec=5
# The service needs no write access to anything except its own logs.
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now pme-care
```

Bind to `127.0.0.1`, then put **nginx or Caddy in front** to terminate TLS. Do
not expose uvicorn directly: the app sets `Strict-Transport-Security` on every
response, which is a promise it cannot keep over plain HTTP.

Caddy is two lines and gets you a certificate automatically:

```
pme.yourplant.local {
    reverse_proxy 127.0.0.1:8080
}
```

Whatever you use, it must set `X-Forwarded-For` — the audit trail reads the
client IP from it.

### 4. Firebase, the secret, and the first admin

These are the same for both shapes: see [Shared setup](#shared-setup) below, then
[Verifying a deploy](#verifying-a-deploy).

### 5. Backups are now yours

Nothing does this for you. A nightly dump, kept off the machine:

```bash
# /etc/cron.daily/pme-care-backup
PGPASSWORD=... pg_dump -h 127.0.0.1 -U pme -Fc pme_care \
  > /var/backups/pme-care-$(date +%F).dump
```

**Restore one before you need to.** An untested backup is not a backup — restore
into a scratch database and count rows. This is the single most likely way to lose
this data.

### The container, if you prefer one

The image carries the API *and* the interface, so `docker compose` is a complete
deployment. Build from the repository root:

```bash
docker build -f backend/Dockerfile -t pme-care .
```

Migrations still run as a separate step, for the reason given under B.

---

## B. Cloud Run + Neon

Target: Cloud Run for the app, Neon for Postgres, Firebase Auth for identity.

Everything here is written to be run in order on a fresh project. Substitute
`PROJECT_ID` and `REGION` (`asia-south1` for an India-based deployment) — or
export them and paste as-is:

```bash
export PROJECT_ID=your-project
export REGION=asia-south1
```

## Shared setup

Needed by both shapes. Steps marked *(Cloud Run only)* can be skipped on your
own server, where the equivalent is `.env` and a filesystem.

**1. Database (Cloud Run only).** Create a Neon project and take the **pooled** connection
string. Change `postgresql://` to `postgresql+psycopg://` so SQLAlchemy picks
psycopg 3, and keep `?sslmode=require`.

**2. Store it as a secret (Cloud Run only).** On your own server this lives in
`.env` with mode 600. The connection string carries the database
password, so it never goes in the service YAML, the image, or git:

```bash
gcloud secrets create pme-care-database-url --replication-policy=automatic
printf '%s' 'postgresql+psycopg://USER:PASS@HOST/DB?sslmode=require' \
  | gcloud secrets versions add pme-care-database-url --data-file=-
```

**3. Service account (Cloud Run only)**, with only what the service needs — reading its own
secret and nothing else:

```bash
gcloud iam service-accounts create pme-care-api --display-name "PME Care API"
SA=pme-care-api@$PROJECT_ID.iam.gserviceaccount.com

gcloud secrets add-iam-policy-binding pme-care-database-url \
  --member="serviceAccount:$SA" --role=roles/secretmanager.secretAccessor

# Creating Firebase users and generating sign-in links:
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:$SA" --role=roles/firebaseauth.admin
```

The Firebase Admin SDK authenticates through Application Default Credentials,
which on Cloud Run means this service account. No key file is ever downloaded —
if you find yourself creating a JSON key, something has gone wrong.

**4. Firebase.** In the console, enable **Email/Password** and **Email link
(passwordless sign-in)** as providers, and add your frontend domain to the
authorized domains list. Sign-in links are rejected from unlisted domains.

## Deploying to Cloud Run

**1. Build and push:**

```bash
gcloud artifacts repositories create pme-care --repository-format=docker --location=$REGION
# Context is the repository root, so the image carries the interface too.
gcloud builds submit . --config=- <<CFG
steps:
  - name: gcr.io/cloud-builders/docker
    args: [build, -f, backend/Dockerfile, -t, "$REGION-docker.pkg.dev/$PROJECT_ID/pme-care/api:$(git rev-parse --short HEAD)", .]
images: ["$REGION-docker.pkg.dev/$PROJECT_ID/pme-care/api:$(git rev-parse --short HEAD)"]
CFG
```

Tag with the commit, not `latest`: a rollback needs a specific image to go back
to, and `latest` moves.

**2. Migrate — as a separate step, before the new revision serves.**

```bash
DATABASE_URL="$(gcloud secrets versions access latest --secret=pme-care-database-url)" \
  .venv/bin/alembic upgrade head
```

This is deliberately not run by the container. A container that migrates on boot
migrates again on every autoscale event, and during a rollback it would migrate
*forward* while you are trying to go back.

Migrations here are additive — new columns are nullable, new indexes are
partial — so an old revision keeps working against the new schema for the
duration of a deploy. Keep it that way: a column rename is two deploys, not one.

**3. Deploy the revision:**

```bash
sed -e "s/PROJECT_ID/$PROJECT_ID/g" -e "s/REGION/$REGION/g" infra/cloudrun-service.yaml \
  | gcloud run services replace - --region=$REGION
```

**4. Create the first administrator.** Every account-creating endpoint requires
an authenticated admin, and a fresh database has none — so this once, from a
machine with access to the secret:

```bash
DATABASE_URL="$(gcloud secrets versions access latest --secret=pme-care-database-url)" \
AUTH_FAKE_MODE=false ENV=production FIREBASE_PROJECT_ID=$PROJECT_ID \
GOOGLE_APPLICATION_CREDENTIALS=... \
  .venv/bin/python -m scripts.bootstrap_admin --email ops@example.com --name "Ops Admin"
```

It prints a sign-in link to send them, and refuses to run once an active admin
exists — so it cannot quietly become a second way in. Everyone else is created
by that admin through `POST /users`.

## Verifying a deploy

Works for either shape — set `URL` to your own host
(`URL=https://pme.yourplant.local`) or ask Cloud Run for it:

```bash
URL=$(gcloud run services describe pme-care-api --region=$REGION --format='value(status.url)')

curl -s $URL/api/v1/health        # {"status":"ok",...,"env":"production"}
curl -s $URL/api/v1/health/live   # {"status":"alive",...}
curl -s -o /dev/null -w '%{http_code}\n' $URL/docs           # must be 404
curl -s -o /dev/null -w '%{http_code}\n' $URL/openapi.json   # must be 404
curl -s -o /dev/null -w '%{http_code}\n' $URL/api/v1/me      # must be 401
curl -s -o /dev/null -w '%{http_code}\n' $URL/               # the interface: 200
```

The last three matter. `ENV=production` is what turns off the API schema, and
`/api/v1/me` returning 401 rather than 200 is how you know `AUTH_FAKE_MODE` is
genuinely off — with it on, any string would be accepted as a uid.

For a fuller check that also drives the actual rendered page — the demo banner,
a real sign-in, whether it scrolls sideways on a phone — none of which a status
code can prove:

```bash
pip install websocket-client   # scripts/verify_deployment.py's only extra dependency
python scripts/verify_deployment.py "$URL"
```

It reports pass/fail per check and exits non-zero on any failure, so it works
equally as a manual gate today or a pipeline step later.

## Rolling back

```bash
gcloud run services update-traffic pme-care-api --region=$REGION --to-revisions=REVISION=100
```

Do **not** run `alembic downgrade` as part of a rollback unless you have
established that the specific revision is reversible without data loss — 0004
cancels duplicate examinations on the way up and cannot restore them, and 0005's
downgrade drops `next_due_date` and every date in it. Because migrations are
additive, the previous image runs against the newer schema, so a traffic
rollback alone is usually the whole job.

## Probes

`/api/v1/health` touches the database and is the **readiness/startup** probe: an
instance that cannot reach Postgres should not receive traffic.

`/api/v1/health/live` deliberately does not, and is the **liveness** probe. A
database outage is not a reason to kill containers — restarting every instance
during a Neon blip turns a brief dependency failure into an outage, and the
replacements cannot reach the database either.

## Operational notes

**Free-tier limits shape the config.** `maxScale: 4` caps how many pools exist
at once, because Neon's free tier tolerates few connections and each instance
holds `DB_POOL_SIZE` of them. `pool_pre_ping` is on so a connection killed during
Neon's auto-suspend is replaced rather than raising.

**Cold starts** hit the first request after a scale-to-zero. `minScale: 1`
removes them at the cost of leaving the free tier.

**`audit_logs` grows without bound** and nothing prunes it. That is correct for
an audit trail, but it is the table that will eventually dominate the database,
and a retention policy is a decision nobody has made yet.

**Logs go to stdout** as plain text, so Cloud Logging will not parse severity or
the request id as structured fields. Every line carries `rid=` and every error
response carries the same `request_id`, so a user-reported failure can still be
grepped to one line.

## Not yet addressed

- **No rate limiting.** Firebase verifies tokens, so this is not a
  credential-stuffing surface, but nothing stops an authenticated client
  hammering an endpoint.
- **Audit IP is spoofable** — `X-Forwarded-For` is trusted as-is. Fine for
  debugging, weak if the trail is ever treated as evidence. Cloud Run's own
  load balancer appends the real caller, so the left-most entry can be a lie;
  reading the *last* entry instead would be the fix.
- **No backup verification.** Neon takes its own; nobody has restored one.
