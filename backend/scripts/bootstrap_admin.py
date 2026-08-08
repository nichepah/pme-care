"""Create the first administrator, so a fresh deployment is usable.

Every account-creating endpoint requires an authenticated ADMIN. On an empty
database there is none, so nobody can create one — a deadlock that made a
production deployment inert no matter how correct the rest of the API was. This
script is the way in, run once by whoever operates the deployment:

    python -m scripts.bootstrap_admin --email ops@example.com --name "Ops Admin"

It refuses to run when an active administrator already exists, so it cannot be
used to quietly mint a second one later. To replace a lost admin, deactivate the
old row first — that way the act is visible in the audit trail.

In production it creates the Firebase identity and prints the sign-in link. In
AUTH_FAKE_MODE it prints a bearer token you can use immediately.
"""

import argparse
import sys

from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal
from app.models import User, UserRole, record_audit
from app.provisioning import ProvisioningError, get_provisioner


def _existing_admin(db) -> User | None:
    """Return an active administrator if the deployment already has one."""
    return db.execute(select(User).where(User.role == UserRole.ADMIN,
                                        User.is_active.is_(True),
                                        User.deleted_at.is_(None))).scalars().first()


def create_first_admin(email: str, display_name: str) -> int:
    """Provision the first ADMIN account. Returns a process exit code."""
    with SessionLocal() as db:
        if (existing := _existing_admin(db)) is not None:
            print(f"Refusing: an active administrator already exists ({existing.email}).\n"
                  "Deactivate it through the API first if you need to replace it.",
                  file=sys.stderr)
            return 1

        taken = db.execute(select(User).where(User.email == email,
                                             User.deleted_at.is_(None))).scalar_one_or_none()
        if taken is not None:
            print(f"Refusing: {email} is already registered as {taken.role.value}.", file=sys.stderr)
            return 1

        try:
            identity = get_provisioner("admin").create(db, email=email, display_name=display_name)
        except ProvisioningError as exc:
            print(f"Identity provider refused: {exc.message}", file=sys.stderr)
            return 2

        admin = User(firebase_uid=identity.firebase_uid, email=email, role=UserRole.ADMIN,
                     display_name=display_name)
        db.add(admin)
        db.flush()
        # actor is None: this happened outside any request, by an operator with
        # database access rather than an authenticated user (AUD-1).
        record_audit(db, None, "CREATE", "user", admin.id,
                     summary={"role": "ADMIN", "via": "bootstrap_admin"})
        db.commit()

        print(f"Created administrator {display_name} <{email}>")
        if identity.dev_bearer_token:
            print(f"  AUTH_FAKE_MODE bearer token: {identity.dev_bearer_token}")
        elif identity.sign_in_link:
            print(f"  Send this sign-in link to the new admin:\n    {identity.sign_in_link}")
        else:
            print("  No sign-in link was generated — have them use the app's "
                  "e-mail sign-in flow for this address.")
        return 0


def main() -> int:
    """Parse arguments and create the account."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--email", required=True, help="the administrator's e-mail address")
    parser.add_argument("--name", required=True, dest="display_name", help="their display name")
    args = parser.parse_args()

    mode = "AUTH_FAKE_MODE" if settings.AUTH_FAKE_MODE else f"Firebase ({settings.FIREBASE_PROJECT_ID})"
    print(f"Bootstrapping against {settings.ENV} using {mode}")
    return create_first_admin(args.email, args.display_name)


if __name__ == "__main__":
    raise SystemExit(main())
