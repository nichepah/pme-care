/**
 * What an administrator sees: accounts, and the audit trail.
 *
 * An admin also has every Health Team permission in the API, so those views
 * appear in their nav too — this module only adds what is admin-only.
 *
 * The audit trail is filterable by record on purpose: "what happened to this
 * employee?" is the question it exists to answer, and scrolling a global feed
 * does not answer it.
 */

import { api } from "../api.js";
import { getUser } from "../session.js";
import {
  badge, card, clearErrors, el, field, formatDate, input, pager, render, select,
  showError, spinner, table, toast,
} from "../ui.js";

export async function accountsView(mount) {
  const listHost = el("div", {}, [spinner("Loading accounts")]);
  const state = { role: "", is_active: "", page: 1 };

  const reload = async () => {
    render(listHost, spinner("Loading accounts"));
    try {
      const page = await api.users({ ...state, size: 20 });
      render(listHost,
        table([
          ["Name", (u) => u.display_name],
          ["E-mail", (u) => u.email],
          ["Role", (u) => badge(roleLabel(u.role))],
          ["Status", (u) => u.is_active ? badge("Active", "ok") : badge("Deactivated", "bad")],
          ["Last signed in", (u) => u.last_login_at ? formatDate(u.last_login_at) : "Never"],
          ["", (u) => accountActions(u, reload)],
        ], page.items, "No accounts match."),
        pager(page, (p) => { state.page = p; reload(); }));
    } catch (error) {
      showError(error);
    }
  };

  const roleSelect = select([
    ["", "All roles"], ["DOCTOR", "Doctors"], ["HEALTH_TEAM", "Health Team"],
    ["ADMIN", "Administrators"], ["EMPLOYEE", "Employees"],
  ], { onChange: (e) => { state.role = e.target.value; state.page = 1; reload(); } });

  const statusSelect = select([
    ["", "Active and deactivated"], ["true", "Active only"], ["false", "Deactivated only"],
  ], { onChange: (e) => { state.is_active = e.target.value; state.page = 1; reload(); } });

  render(mount,
    card("Accounts",
      el("div.row", {}, [field("Role", roleSelect), field("Status", statusSelect)]),
      listHost),
    createAccountCard(reload));

  reload();
}

/**
 * Actions on one account.
 *
 * An admin cannot deactivate their own account — the API refuses, and offering
 * the button anyway would be an invitation to a 403. Employee accounts get no
 * role control either: their role follows their employee record.
 */
function accountActions(account, reload) {
  const isSelf = account.id === getUser()?.id;
  if (isSelf) return el("span.field-hint", { text: "This is you" });

  return el("button.ghost.small", {
    text: account.is_active ? "Deactivate" : "Reactivate",
    onClick: async (event) => {
      if (account.is_active && !window.confirm(
        `Deactivate ${account.display_name}? They will not be able to sign in.`)) return;
      event.target.disabled = true;
      try {
        await api.updateUser(account.id, { is_active: !account.is_active });
        toast(account.is_active ? "Account deactivated." : "Account reactivated.", "ok");
        reload();
      } catch (error) {
        showError(error);
        event.target.disabled = false;
      }
    },
  });
}

function createAccountCard(onCreated) {
  const submit = el("button.primary", { type: "submit", text: "Create account" });
  const form = el("form", {
    onSubmit: async (event) => {
      event.preventDefault();
      clearErrors(form);
      submit.disabled = true;
      const value = (name) => form.querySelector(`[name="${name}"]`).value.trim();
      try {
        const created = await api.createUser({
          email: value("email"),
          display_name: value("display_name"),
          role: value("role"),
        });
        // Dev mode hands back a usable token; production hands back a link for
        // the person to follow. Say which, rather than a generic "created".
        if (created.dev_bearer_token) {
          toast(`Created. Development token: ${created.dev_bearer_token}`, "ok");
        } else if (created.sign_in_link) {
          toast("Created. Send them the sign-in link that was generated.", "ok");
        } else {
          toast("Created. They can sign in with their e-mail address.", "ok");
        }
        form.reset();
        onCreated();
      } catch (error) {
        showError(error, form);
      } finally {
        submit.disabled = false;
      }
    },
  }, [
    el("div.row", {}, [
      field("Full name", input({ name: "display_name", required: true })),
      field("E-mail", input({ name: "email", type: "email", required: true })),
    ]),
    field("Role", select([
      ["DOCTOR", "Doctor"], ["HEALTH_TEAM", "Health Team"], ["ADMIN", "Administrator"],
    ], { name: "role" }),
      "Employee logins are created from the employee's own record, so that each " +
      "one stays linked to a person."),
    submit,
  ]);
  return card("Create a staff account", form);
}

export async function auditView(mount, params) {
  const listHost = el("div", {}, [spinner("Loading the trail")]);
  const state = {
    entity_type: params.entity_type ?? "",
    entity_id: params.entity_id ?? "",
    action: "",
    page: 1,
  };

  const reload = async () => {
    render(listHost, spinner("Loading the trail"));
    try {
      const page = await api.auditLogs({ ...state, size: 25 });
      render(listHost,
        table([
          ["When", (r) => new Date(r.created_at).toLocaleString()],
          ["Who", (r) => badge(roleLabel(r.actor_role) ?? "System")],
          ["Did", (r) => r.action],
          ["To", (r) => r.entity_type],
          ["Which fields", (r) => summarise(r.summary)],
          ["From", (r) => r.ip_address ?? "—"],
        ], page.items, "No matching audit entries."),
        pager(page, (p) => { state.page = p; reload(); }));
    } catch (error) {
      showError(error);
    }
  };

  const typeSelect = select([
    ["", "Everything"], ["employee", "Employee records"],
    ["examination", "Examinations"], ["user", "Accounts"],
  ], {
    value: state.entity_type,
    onChange: (e) => { state.entity_type = e.target.value; state.page = 1; reload(); },
  });

  const recordInput = input({
    placeholder: "Paste a record id to see only its history",
    value: state.entity_id,
    onChange: (e) => { state.entity_id = e.target.value.trim(); state.page = 1; reload(); },
  });

  render(mount, card("Audit trail",
    el("p.field-hint", {
      text: "Records which fields changed, never the values — no blood pressure or " +
            "diagnosis text is ever stored here.",
    }),
    el("div.row", {}, [field("Show", typeSelect), field("One record only", recordInput)]),
    listHost));

  reload();
}

/** The changed-field names, as a readable phrase. */
function summarise(summary) {
  if (!summary) return "—";
  if (Array.isArray(summary.fields_changed)) return summary.fields_changed.join(", ");
  return Object.entries(summary).map(([k, v]) => `${k}: ${v}`).join(", ");
}

function roleLabel(role) {
  return {
    EMPLOYEE: "Employee", DOCTOR: "Doctor",
    HEALTH_TEAM: "Health Team", ADMIN: "Administrator",
  }[role] ?? role;
}
