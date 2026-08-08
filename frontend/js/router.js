/**
 * Hash routing with role guards.
 *
 * Each route declares which roles may see it, and the nav is built from the same
 * table — so a Doctor is never shown a link to the audit trail *and* cannot
 * reach it by typing the URL. This is convenience and tidiness, not security:
 * the API enforces every rule again, because anything in a browser can be edited.
 */

import { getUser, hasRole } from "./session.js";
import { el, render } from "./ui.js";

/**
 * @typedef Route
 * @property {string} path      hash path, e.g. "/employees"
 * @property {string[]} roles   roles allowed; empty means "any signed-in user"
 * @property {string} [nav]     label in the nav bar; omitted routes stay unlisted
 * @property {Function} view    async (mountNode, params) => void
 */

const routes = [];
let mount = null;
let onNavChange = () => {};

export function defineRoutes(list) {
  routes.length = 0;
  routes.push(...list);
}

export function start(mountNode, navRenderer) {
  mount = mountNode;
  onNavChange = navRenderer;
  window.addEventListener("hashchange", handleRoute);
  handleRoute();
}

export function navigate(path) {
  if (window.location.hash === `#${path}`) handleRoute();
  else window.location.hash = `#${path}`;
}

/** Routes the signed-in user may actually see, for building the nav. */
export function visibleRoutes() {
  return routes.filter((r) => r.nav && (r.roles.length === 0 || hasRole(...r.roles)));
}

/** Where a role should land after signing in: its first navigable route. */
export function homePathFor() {
  return visibleRoutes()[0]?.path ?? "/me";
}

function parse() {
  const raw = window.location.hash.replace(/^#/, "") || "/";
  const [path, search] = raw.split("?");
  return { path, params: Object.fromEntries(new URLSearchParams(search ?? "")) };
}

async function handleRoute() {
  const { path, params } = parse();
  const user = getUser();

  if (!user) {
    const { loginView } = await import("./views/login.js");
    onNavChange();
    return loginView(mount);
  }

  if (path === "/" || path === "/login") return navigate(homePathFor());

  const route = routes.find((r) => r.path === path);
  if (!route) return renderMessage("That page does not exist.", true);
  if (route.roles.length && !hasRole(...route.roles)) {
    // Same posture as the API: do not confirm that a page exists for someone else.
    return renderMessage("That page does not exist.", true);
  }

  onNavChange();
  try {
    await route.view(mount, params);
  } catch (error) {
    // A 401 has already redirected to login; anything else is worth showing
    // rather than leaving a blank screen.
    if (error?.status !== 401) {
      renderMessage(error?.message ?? "Something went wrong loading this page.");
    }
  }
}

function renderMessage(message, withHome = false) {
  render(mount, el("section.card", {}, [
    el("p.empty", { text: message }),
    withHome
      ? el("button.primary", { text: "Go to my home page", onClick: () => navigate(homePathFor()) })
      : null,
  ]));
}
