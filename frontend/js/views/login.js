/**
 * Signing in.
 *
 * In development the bearer token *is* the firebase_uid, so this asks for that
 * string and offers the seeded accounts as buttons — pasting uids by hand is how
 * the old console worked and it was miserable.
 *
 * In production this screen is where Firebase sign-in goes. The seam is
 * `signInWithFirebase` in session.js; it throws rather than pretending, so this
 * cannot ship looking finished when it is not. Which mode is live is detected
 * from the API's own `/health`, not from a flag in the browser.
 */

import { API_BASE } from "../api.js";
import { signInWithToken } from "../session.js";
import { card, el, field, input, render, showError, toast } from "../ui.js";

const DEMO_ACCOUNTS = [
  ["dev-healthteam", "Health Team"],
  ["dev-doctor", "Doctor"],
  ["dev-employee", "Employee"],
  ["dev-admin", "Administrator"],
];

export async function loginView(mount) {
  // Ask the API what environment it is; do not guess from the browser.
  let environment = null;
  try {
    const response = await fetch(`${API_BASE}/health`);
    environment = (await response.json()).env;
  } catch {
    render(mount, card("Cannot reach the service",
      el("p.empty", {
        text: `No response from ${API_BASE}. If you are running this locally, ` +
              "start the backend and reload.",
      })));
    return;
  }

  const isDevelopment = environment !== "production";
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
