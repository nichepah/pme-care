# Deploying PME Care

Target: Cloud Run for the API, Neon for Postgres, Firebase Auth for identity.

Everything here is written to be run in order on a fresh project. Substitute
`PROJECT_ID` and `REGION` (`asia-south1` for an India-based deployment) — or
export them and paste as-is:

```bash
export PROJECT_ID=your-project
export REGION=asia-south1
```

## Before the first deploy

**1. Database.** Create a Neon project and take the **pooled** connection
string. Change `postgresql://` to `postgresql+psycopg://` so SQLAlchemy picks
psycopg 3, and keep `?sslmode=require`.

**2. Store it as a secret.** The connection string carries the database
password, so it never goes in the service YAML, the image, or git:

```bash
gcloud secrets create pme-care-database-url --replication-policy=automatic
printf '%s' 'postgresql+psycopg://USER:PASS@HOST/DB?sslmode=require' \
  | gcloud secrets versions add pme-care-database-url --data-file=-
```

**3. Service account**, with only what the service needs — reading its own
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

## Deploying

**1. Build and push:**

```bash
gcloud artifacts repositories create pme-care --repository-format=docker --location=$REGION
gcloud builds submit backend \
  --tag $REGION-docker.pkg.dev/$PROJECT_ID/pme-care/api:$(git rev-parse --short HEAD)
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

```bash
URL=$(gcloud run services describe pme-care-api --region=$REGION --format='value(status.url)')

curl -s $URL/api/v1/health        # {"status":"ok",...,"env":"production"}
curl -s $URL/api/v1/health/live   # {"status":"alive",...}
curl -s -o /dev/null -w '%{http_code}\n' $URL/docs           # must be 404
curl -s -o /dev/null -w '%{http_code}\n' $URL/openapi.json   # must be 404
curl -s -o /dev/null -w '%{http_code}\n' $URL/api/v1/me      # must be 401
```

The last three matter. `ENV=production` is what turns off the API schema, and
`/api/v1/me` returning 401 rather than 200 is how you know `AUTH_FAKE_MODE` is
genuinely off — with it on, any string would be accepted as a uid.

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
