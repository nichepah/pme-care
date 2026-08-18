/**
 * Signing in.
 *
 * In development (and in a public demo) the bearer token *is* the firebase_uid,
 * so this asks for that string and offers the seeded accounts as buttons —
 * pasting uids by hand is how the old console worked and it was miserable.
 *
 * In production this screen is where Firebase sign-in goes. The seam is
 * `signInWithFirebase` in session.js; it throws rather than pretending, so this
 * cannot ship looking finished when it is not. Which mode is live is read from
 * the API's own `/health` (via `checkHealth`), never guessed from the browser.
 *
 * A `demo` instance gets the strongest wording of the three: it is reachable by
 * anyone, the one-click buttons below sign in as any role including
 * Administrator, and `AUTH_FAKE_MODE` accepts those tokens with no password —
 * so this is the one screen that has to say, before anyone clicks anything,
 * that nothing typed here is private and nothing shown here is real.
 */

import { API_BASE, checkHealth } from "../api.js";
import { signInWithToken } from "../session.js";
import { card, el, field, input, render, showError, text, toast } from "../ui.js";

const DEMO_ACCOUNTS = [
  ["dev-healthteam", "Health Team"],
  ["dev-doctor", "Doctor"],
  ["dev-employee", "Employee"],
  ["dev-admin", "Administrator"],
];

export async function loginView(mount) {
  const health = await checkHealth();
  if (!health.reachable) {
    render(mount, card("Cannot reach the service",
      el("p.empty", {
        text: `No response from ${API_BASE}. If you are running this locally, ` +
              "start the backend and reload.",
      })));
    return;
  }

  const isDevelopment = health.env !== "production";
  const tokenInput = input({
    name: "token",
    placeholder: isDevelopment ? "e.g. dev-healthteam" : "Your Firebase ID token",
    autocomplete: "off",
  });
  const submit = el("button.primary", { type: "submit", text: "Sign in" });

  const form = el("form", {
    onSubmit: async (event) => {
      event.preventDefault();
      submit.disabled = true;
      try {
        await enter(tokenInput.value);
      } catch (error) {
        showError(error, form);
        submit.disabled = false;
      }
    },
  }, [
    field(isDevelopment ? "Development sign-in token" : "ID token", tokenInput),
    submit,
  ]);

  render(mount, el("div.login", {}, [
    health.demo ? demoNotice() : null,
    card("PME Care",
      el("p.field-hint", {
        text: isDevelopment
          ? "Development mode: the token is the account's Firebase uid. Pick one below."
          : "Sign in with the link sent to your work e-mail address.",
      }),
      form,
      isDevelopment ? demoButtons() : productionNotice()),
  ]));
}

/**
 * The thing a stranger has to read before they can click anything. Deliberately
 * blunter than the persistent header banner (js/app.js) — that one is a
 * reminder for someone already using the app; this is the one chance to stop
 * someone from treating it as real before they start.
 */
function demoNotice() {
  return el("div.card.demo-notice", {}, [
    el("p", {}, [
      el("strong", { text: "This is a public demo." }),
      text(" Anyone with this link can sign in as any role, including "
          + "Administrator, using the buttons below — there is no password. "
          + "Every record here is invented. Never enter a real person's "
          + "information."),
    ]),
  ]);
}

function demoButtons() {
  return el("div", {}, [
    el("p.field-label", { text: "Or sign in as one of the seeded accounts" }),
    el("div.actions", {}, DEMO_ACCOUNTS.map(([token, label]) =>
      el("button.ghost.small", {
        text: label,
        onClick: async (event) => {
          event.target.disabled = true;
          try {
            await enter(token);
          } catch (error) {
            showError(error);
            event.target.disabled = false;
          }
        },
      }))),
    el("p.field-hint", {
      text: "These exist only because scripts/seed_dev.py created them.",
    }),
  ]);
}

function productionNotice() {
  return el("p.field-hint.warn-text", {
    text: "Firebase web sign-in is not wired up in this build. Until it is, an ID " +
          "token obtained elsewhere can be pasted above — the backend verifies it " +
          "properly.",
  });
}

/** Adopt a token, then hand over to the router, which decides where to land. */
async function enter(token) {
  if (!token.trim()) {
    toast("Enter a token to sign in.", "error");
    return;
  }
  const user = await signInWithToken(token);
  toast(`Signed in as ${user.display_name}.`, "ok");
  const { navigate, homePathFor } = await import("../router.js");
  navigate(homePathFor());
}
