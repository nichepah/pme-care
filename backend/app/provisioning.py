"""Creating and revoking the Firebase side of an account.

This is the seam that used to block production. Account creation invented a
``firebase_uid`` and refused to run unless AUTH_FAKE_MODE was on — which meant
that with the flag off there was no way to create any account at all, including
the first administrator. Nobody could sign in to a real deployment.

There are now two implementations behind one interface:

* ``FakeProvisioner`` — synthesizes a uid, for local development and tests.
  The uid doubles as the bearer token, since fake-mode auth treats them as the
  same thing.
* ``FirebaseProvisioner`` — calls the Firebase Admin SDK for real. The account
  is created without a password; the user receives a sign-in link and sets
  their own credential, so no password ever passes through this service.

Routes call ``get_provisioner()`` and never import ``firebase_admin``
themselves, which is what makes the whole flow testable without a Firebase
project.
"""

import logging
import re
import uuid
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import AppError
from app.config import settings
from app.models import User

logger = logging.getLogger("pme.provisioning")


class ProvisioningError(AppError):
    """502 — the identity provider refused or was unreachable.

    Deliberately not a 500: the request was valid and this service worked
    correctly. Something downstream did not, and the caller can retry.
    """

    status_code, code = 502, "IDENTITY_PROVIDER_ERROR"


@dataclass(frozen=True)
class ProvisionedAccount:
    """The result of creating an identity.

    Attributes:
        firebase_uid: The identity's uid, stored on the ``users`` row.
        dev_bearer_token: In fake mode, a token that authenticates immediately.
            None in production — there the user signs in through Firebase and
            this service never holds a credential for them.
        sign_in_link: Where a real user completes sign-up, when the provider
            generated one.

    """

    firebase_uid: str
    dev_bearer_token: str | None = None
    sign_in_link: str | None = None


class Provisioner(Protocol):
    """What the routes need from an identity provider."""

    def create(self, db: Session, email: str, display_name: str) -> ProvisionedAccount:
        """Create an identity for this address and return its uid."""
        ...

    def revoke(self, firebase_uid: str) -> None:
        """Stop this identity from authenticating, if the provider can."""
        ...


def _slug(seed: str) -> str:
    """Reduce arbitrary text to a uid-safe fragment."""
    return re.sub(r"[^a-z0-9]+", "-", seed.strip().lower()).strip("-") or "account"


class FakeProvisioner:
    """Development/test implementation: no network, no Firebase project.

    The uid is derived from a readable seed so that a token pasted into the demo
    UI is recognisable (``emp-p-1001``, ``doctor-dr-rao``) rather than random.
    """

    def __init__(self, prefix: str = "user") -> None:
        """Set the prefix that groups the generated uids by kind."""
        self.prefix = prefix

    def create(self, db: Session, email: str, display_name: str) -> ProvisionedAccount:
        """Synthesize an unused uid; it is also the bearer token in fake mode."""
        uid = f"{self.prefix}-{_slug(display_name)}"[:120]
        if db.execute(select(User).where(User.firebase_uid == uid)).scalar_one_or_none() is not None:
            uid = f"{uid}-{uuid.uuid4().hex[:6]}"
        return ProvisionedAccount(firebase_uid=uid, dev_bearer_token=uid)

    def revoke(self, firebase_uid: str) -> None:
        """Nothing to revoke: deactivating the ``users`` row is what stops
        fake-mode auth, and it has already happened by the time this is called."""


class FirebaseProvisioner:
    """Production implementation, backed by the Firebase Admin SDK.

    Accounts are created with no password and ``email_verified=False``. The user
    proves the address and chooses a credential via the sign-in link, so this
    service never sees, stores or transmits a password.
    """

    def __init__(self, prefix: str = "user") -> None:
        """Prefix is unused here — Firebase allocates the uid."""
        self.prefix = prefix

    def create(self, db: Session, email: str, display_name: str) -> ProvisionedAccount:
        """Create the Firebase user and generate a sign-in link.

        Raises:
            ProvisioningError: the address is already in use, or the SDK failed.

        """
        from firebase_admin import auth as fb_auth

        try:
            record = fb_auth.create_user(email=email, display_name=display_name,
                                         email_verified=False)
        except fb_auth.EmailAlreadyExistsError as exc:
            raise ProvisioningError(
                f"A Firebase account already exists for {email}. Link it instead of "
                "creating a new one.") from exc
        except Exception as exc:
            raise ProvisioningError("Could not create the account with the identity "
                                    "provider. Try again.") from exc

        from firebase_admin.auth import ActionCodeSettings

        # Read config and build the request *outside* the try below, so a
        # misconfiguration fails loudly here instead of being mistaken for a
        # provider outage and quietly producing no link forever.
        action = ActionCodeSettings(url=settings.SIGN_IN_CONTINUE_URL)
        try:
            link = fb_auth.generate_sign_in_with_email_link(email, action)
        except Exception as exc:  # noqa: BLE001 — provider-side only; see below
            # The account already exists at this point, so raising would leave an
            # orphaned Firebase user behind. Log it and hand back an account with
            # no link: the user can still get in through the app's normal e-mail
            # sign-in flow, and this line is the trace if nobody can.
            logger.warning("Created %s but could not generate a sign-in link: %s", email, exc)
            link = None

        return ProvisionedAccount(firebase_uid=record.uid, dev_bearer_token=None,
                                  sign_in_link=link)

    def revoke(self, firebase_uid: str) -> None:
        """Disable the Firebase user and invalidate its refresh tokens.

        Deactivating the local row already blocks this API, but a live Firebase
        session could still mint fresh ID tokens for other services sharing the
        project. Failures are swallowed: the local revocation is what this API
        enforces, and it has already succeeded.
        """
        try:
            from firebase_admin import auth as fb_auth

            fb_auth.update_user(firebase_uid, disabled=True)
            fb_auth.revoke_refresh_tokens(firebase_uid)
        except Exception:  # noqa: BLE001 — best effort; local deactivation stands
            pass


def get_provisioner(prefix: str = "user") -> Provisioner:
    """Return the provisioner matching the current auth mode.

    Args:
        prefix: Groups generated uids by kind in fake mode (``emp``, ``doctor``).

    """
    if settings.AUTH_FAKE_MODE:
        return FakeProvisioner(prefix)
    return FirebaseProvisioner(prefix)
