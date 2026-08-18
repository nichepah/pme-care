/**
 * The route table — where "what does this role need?" is actually answered.
 *
 * Read top to bottom, this is the product decision. Each role's first route is
 * its home page, so signing in lands you on the thing your job starts with:
 *
 *   Employee     one screen: am I fit, when is my next examination
 *   Doctor       the worklist, leading into the examination form
 *   Health Team  the compliance list — who has lapsed — then search and register
 *   Admin        everything the Health Team has, plus accounts and the audit trail
 *
 * What each role does *not* get is as deliberate. A doctor cannot register
 * employees or cancel bookings; the Health Team cannot record a clinical
 * decision; an employee sees no list of any kind. Those are the API's rules, and
 * the nav simply stops offering what would be refused anyway.
 */

import { checkHealth } from "./api.js";
import { clearSession, getUser, restoreSession } from "./session.js";
import { defineRoutes, navigate, start, visibleRoutes } from "./router.js";
import { el, render } from "./ui.js";

import { accountsView, auditView } from "./views/admin.js";
import { doctorWorklistView, examineView } from "./views/doctor.js";
import { myStatusView } from "./views/employee.js";
import {
  complianceView, employeeDetailView, employeesView, scheduledView,
} from "./views/healthteam.js";

const HEALTH_TEAM = ["HEALTH_TEAM", "ADMIN"];
const STAFF = ["HEALTH_TEAM", "DOCTOR", "ADMIN"];

defineRoutes([
  // Employee. Deliberately first for them and unreachable for anyone else's nav.
  { path: "/me", roles: ["EMPLOYEE"], nav: "My status", view: myStatusView },

  // Doctor.
  { path: "/worklist", roles: ["DOCTOR"], nav: "Examinations to do", view: doctorWorklistView },
  { path: "/examine", roles: ["DOCTOR"], view: examineView },

  // Health Team — compliance first, because the job is preventing lapses.
  { path: "/compliance", roles: HEALTH_TEAM, nav: "Who is due", view: complianceView },
  { path: "/employees", roles: HEALTH_TEAM, nav: "Employees", view: employeesView },
  { path: "/employee", roles: STAFF, view: employeeDetailView },
  { path: "/booked", roles: HEALTH_TEAM, nav: "Booked", view: scheduledView },

  // Admin only.
  { path: "/accounts", roles: ["ADMIN"], nav: "Accounts", view: accountsView },
  { path: "/audit", roles: ["ADMIN"], nav: "Audit trail", view: auditView },
]);

const ROLE_LABELS = {
  EMPLOYEE: "Employee",
  DOCTOR: "Doctor",
  HEALTH_TEAM: "Health Team",
  ADMIN: "Administrator",
};

function renderNav() {
  const header = document.getElementById("header");
  const user = getUser();

  if (!user) return render(header, el("div.brand", { text: "PME Care" }));

  const currentPath = window.location.hash.replace(/^#/, "").split("?")[0];

  render(header,
    el("div.brand", { text: "PME Care" }),
    el("nav", {}, visibleRoutes().map((route) =>
      el(`a.navlink${currentPath === route.path ? ".active" : ""}`, {
        href: `#${route.path}`, text: route.nav,
      }))),
    el("div.whoami", {}, [
      el("span.whoami-name", { text: user.display_name }),
      el("span.whoami-role", { text: ROLE_LABELS[user.role] ?? user.role }),
      el("button.ghost.small", {
        text: "Sign out",
        onClick: () => {
          clearSession();
          renderNav();
          navigate("/login");
        },
      }),
    ]));
}

/**
 * Show the demo banner on every screen, not just the login page.
 *
 * The login screen's own notice (js/views/login.js) is the one chance to stop
 * someone before they click anything; this is the reminder for someone already
 * signed in and clicking around that what they're looking at still isn't real.
 * Driven by the server's `/health.demo` field, never a flag the browser could
 * be talked out of.
 */
async function showDemoBannerIfApplicable() {
  const health = await checkHealth();
  if (!health.demo) return;
  const banner = document.getElementById("demo-banner");
  banner.textContent = "Demo — invented data only. Anyone with this link can "
    + "sign in as any role. Nothing shown here is real.";
  banner.classList.add("visible");
}

async function boot() {
  const mount = document.getElementById("app");
  // Resume a session before the first route runs, so a reload does not bounce
  // the user back to the login screen. Runs alongside the banner check rather
  // than after it — neither depends on the other, and a slow /health should
  // not delay getting the signed-in user to their screen.
  await Promise.all([restoreSession(), showDemoBannerIfApplicable()]);
  start(mount, renderNav);
}

boot();
