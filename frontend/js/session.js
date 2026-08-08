/**
 * Who is signed in.
 *
 * The token lives in sessionStorage rather than localStorage: it is a
 * credential, and on a shared plant terminal it should not outlive the browser
 * tab. The identity behind it (`/me`) is fetched, never trusted from storage —
 * a role read out of the browser could be edited, and every check that matters
 * happens server-side anyway.
 *
 * AUTHENTICATION MODES
 * --------------------
 * Dev (AUTH_FAKE_MODE=true): the bearer token *is* the firebase_uid, so signing
 * in means supplying that string. That is what `signInWithToken` does.
 *
 * Production: Firebase issues the ID token in the browser and this module hands
 * it over unchanged — the backend verifies it. Wiring that needs the Firebase
 * JS SDK and a web app config; `signInWithFirebase` below is the seam, and is
 * deliberately unimplemented rather than faked.
 */

const TOKEN_KEY = "pme.token";

let currentUser = null;

/** The bearer token, or null. */
export function getToken() {
  return sessionStorage.getItem(TOKEN_KEY);
}

/** The signed-in user from /me, or null if not signed in yet. */
export function getUser() {
  return currentUser;
}

export function hasRole(...roles) {
  return currentUser !== null && roles.includes(currentUser.role);
}

/** Forget the credential and the identity. */
export function clearSession() {
  sessionStorage.removeItem(TOKEN_KEY);
  currentUser = null;
}

/**
 * Adopt a token and resolve who it belongs to.
 *
 * @returns the user from /me
 * @throws ApiError if the token is not accepted, leaving no session behind
 */
export async function signInWithToken(token) {
  sessionStorage.setItem(TOKEN_KEY, token.trim());
  const { api } = await import("./api.js");
  try {
    currentUser = await api.me();
    return currentUser;
  } catch (error) {
    clearSession();
    throw error;
  }
}

/** Restore a session on page load, if the stored token still works. */
export async function restoreSession() {
  if (!getToken()) return null;
  const { api } = await import("./api.js");
  try {
    currentUser = await api.me();
    return currentUser;
  } catch {
    clearSession();
    return null;
  }
}

/**
 * Production sign-in. Not implemented.
 *
 * The shape it will take: load the Firebase JS SDK, call
 * signInWithEmailLink/signInWithEmailAndPassword, take `await
 * user.getIdToken()`, and pass it to `signInWithToken` — which needs no change,
 * because the backend treats a Firebase ID token and a fake-mode uid the same
 * way: as the bearer value.
 *
 * Left as a throw rather than a stub so it cannot be mistaken for working.
 */
export async function signInWithFirebase() {
  throw new Error(
    "Firebase web sign-in is not wired up yet. Needs the Firebase JS SDK plus a " +
      "web app config; the backend side is ready and verifies ID tokens already.",
  );
}
