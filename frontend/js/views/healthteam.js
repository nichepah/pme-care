/**
 * What the Health Team sees: who needs booking, and the tools to act on it.
 *
 * The landing page is the compliance list rather than a search box, because the
 * job is not "look someone up" — it is "make sure nobody has lapsed". Search
 * exists, but it is the second thing, not the first.
 *
 * Booking happens inline from that list. Making someone copy an id into a
 * separate form is how a worklist stops getting worked.
 */

import { api } from "../api.js";
import { navigate } from "../router.js";
import {
  badge, card, clearErrors, el, field, fitnessKind, fitnessLabel, formatDate, input,
  pager, render, select, showError, spinner, stat, table, toast, today,
} from "../ui.js";

/** The compliance dashboard: counts, then the list itself. */
export async function complianceView(mount) {
  render(mount, spinner("Working out who is due"));

  // Three separate counts because each is a different question. Only totals are
  // needed for the tiles, so size=1 keeps them cheap.
  const [overdue, soon, all] = await Promise.all([
    api.dueEmployees({ overdue_only: true, size: 1 }),
    api.dueEmployees({ within_days: 30, size: 1 }),
    api.dueEmployees({ within_days: 365, size: 1 }),
  ]);

  const listHost = el("div", {}, [spinner("Loading the list")]);
  const filters = {
    within_days: 30,
    overdue_only: "",
    department: "",
    page: 1,
  };

  const reload = async () => {
    render(listHost, spinner("Loading the list"));
    try {
      const page = await api.dueEmployees({ ...filters, size: 20 });
      render(listHost,
        table([
          ["Employee", (e) => `${e.full_name} · ${e.personal_number}`],
          ["Department", (e) => e.department],
          ["Last examined", (e) => formatDate(e.last_exam_date)],
          ["Standing", dueStanding],
          ["", (e) => el("button.primary.small", {
            text: "Book today", onClick: (event) => bookNow(e, event.target, reload),
          })],
        ], page.items, "Nobody is due in this window. Nothing to do."),
        pager(page, (p) => { filters.page = p; reload(); }));
    } catch (error) {
      showError(error);
      render(listHost, el("p.empty", { text: "Could not load the list." }));
    }
  };

  const windowSelect = select([
    ["30", "Due within 30 days"],
    ["90", "Due within 90 days"],
    ["365", "Due within a year"],
    ["0", "Overdue only"],
  ], {
    onChange: (event) => {
      const value = event.target.value;
      filters.overdue_only = value === "0" ? "true" : "";
      filters.within_days = value === "0" ? "" : value;
      filters.page = 1;
      reload();
    },
  });

  const departmentInput = input({
    placeholder: "Any department",
    onInput: debounce((event) => {
      filters.department = event.target.value.trim();
      filters.page = 1;
      reload();
    }),
  });

  render(mount,
    el("div.stats", {}, [
      stat("Overdue now", overdue.total, overdue.total > 0 ? "bad" : "ok"),
      stat("Due within 30 days", soon.total, "warn"),
      stat("Due within a year", all.total),
    ]),
    card("Who needs an examination booked",
      el("div.row", {}, [field("Window", windowSelect), field("Department", departmentInput)]),
      listHost));

  reload();
}

/** How overdue someone is, in words a compliance officer can scan. */
function dueStanding(e) {
  if (e.never_examined) return badge("Never examined", "bad");
  if (e.days_overdue > 0) return badge(`${e.days_overdue} days overdue`, "bad");
  return badge(`Due in ${-e.days_overdue} days`, "warn");
}

/** Book an examination for today, straight from the list. */
async function bookNow(employee, button, reload) {
  button.disabled = true;
  try {
    await api.schedule({ employee_id: employee.id, scheduled_date: today() });
    toast(`Booked for ${employee.full_name}.`, "ok");
    reload();
  } catch (error) {
    showError(error);
    button.disabled = false;
  }
}

/** Search, with the register form alongside. */
export async function employeesView(mount) {
  const listHost = el("div", {}, [spinner("Loading employees")]);
  const state = { q: "", department: "", is_active: "", page: 1 };

  const reload = async () => {
    render(listHost, spinner("Searching"));
    try {
      const page = await api.employees({ ...state, size: 20 });
      render(listHost,
        table([
          ["Number", (e) => e.personal_number],
          ["Name", (e) => e.full_name],
          ["Department", (e) => e.department],
          ["Plant", (e) => e.plant],
          ["Status", (e) => e.is_active ? badge("Active", "ok") : badge("Retired")],
          ["", (e) => el("button.ghost.small", {
            text: "Open", onClick: () => navigate(`/employee?id=${e.id}`),
          })],
        ], page.items, "No employees match."),
        pager(page, (p) => { state.page = p; reload(); }));
    } catch (error) {
      showError(error);
    }
  };

  const search = input({
    placeholder: "Name or personal number",
    onInput: debounce((event) => { state.q = event.target.value.trim(); state.page = 1; reload(); }),
  });
  const activeSelect = select([["", "Active only"], ["false", "Retired only"]], {
    onChange: (event) => { state.is_active = event.target.value; state.page = 1; reload(); },
  });

  render(mount,
    card("Find an employee",
      el("div.row", {}, [field("Search", search), field("Show", activeSelect)]),
      listHost),
    registerCard(reload));

  reload();
}

function registerCard(onRegistered) {
  const submit = el("button.primary", { type: "submit", text: "Register employee" });
  const form = el("form", {
    onSubmit: async (event) => {
      event.preventDefault();
      clearErrors(form);
      submit.disabled = true;
      const value = (name) => form.querySelector(`[name="${name}"]`).value.trim();
      try {
        const created = await api.registerEmployee({
          personal_number: value("personal_number"),
          full_name: value("full_name"),
          department: value("department"),
          plant: value("plant"),
          contact_number: value("contact_number"),
          email: value("email") || null,
        });
        toast(`${created.full_name} registered.`, "ok");
        form.reset();
        onRegistered();
      } catch (error) {
        showError(error, form);
      } finally {
        submit.disabled = false;
      }
    },
  }, [
    el("div.row", {}, [
      field("Personal number", input({ name: "personal_number", required: true })),
      field("Full name", input({ name: "full_name", required: true })),
    ]),
    el("div.row", {}, [
      field("Department", input({ name: "department", required: true })),
      field("Plant", input({ name: "plant", required: true })),
    ]),
    el("div.row", {}, [
      field("Contact number", input({ name: "contact_number", required: true })),
      field("E-mail", input({ name: "email", type: "email" }),
        "Needed later to send them a sign-in link."),
    ]),
    submit,
  ]);
  return card("Register a new employee", form);
}

/** One employee: details, their history, and the actions available on them. */
export async function employeeDetailView(mount, params) {
  if (!params.id) return navigate("/employees");
  render(mount, spinner("Loading the employee"));

  const employee = await api.employee(params.id);
  const historyHost = el("div", {}, [spinner("Loading history")]);

  api.employeeHistory(employee.id, { size: 20 }).then((page) => {
    render(historyHost, table([
      ["Booked", (x) => formatDate(x.scheduled_date)],
      ["Examined", (x) => formatDate(x.exam_date)],
      ["Outcome", (x) => x.status === "COMPLETED"
        ? badge(fitnessLabel(x.fitness_status), fitnessKind(x.fitness_status))
        : badge(x.status === "CANCELLED" ? "Cancelled" : "Booked", x.status === "SCHEDULED" ? "warn" : "")],
      ["Next due", (x) => formatDate(x.next_due_date)],
      ["Notes", (x) => x.remarks ?? x.cancel_reason ?? "—"],
      ["", (x) => x.status === "SCHEDULED"
        ? el("button.ghost.small", { text: "Cancel", onClick: () => cancelExam(x, mount, params) })
        : ""],
    ], page.items, "No examinations yet."));
  });

  render(mount,
    card(employee.full_name,
      el("p.field-hint", {
        text: `${employee.personal_number} · ${employee.department} · ${employee.plant} · ` +
              `${employee.contact_number}${employee.email ? ` · ${employee.email}` : ""}`,
      }),
      el("div.actions", {}, [
        el("button.primary", {
          text: "Book an examination today",
          onClick: async (event) => {
            await bookNow(employee, event.target, () => employeeDetailView(mount, params));
          },
        }),
        el("button.ghost", {
          text: "Give this employee a login",
          onClick: (event) => giveLogin(employee, event.target),
        }),
      ])),
    editCard(employee, () => employeeDetailView(mount, params)),
    card("Examination history", historyHost));
}

function editCard(employee, reload) {
  const submit = el("button.primary", { type: "submit", text: "Save changes" });
  const form = el("form", {
    onSubmit: async (event) => {
      event.preventDefault();
      clearErrors(form);
      submit.disabled = true;
      const value = (name) => form.querySelector(`[name="${name}"]`).value.trim();
      try {
        await api.updateEmployee(employee.id, {
          full_name: value("full_name"),
          department: value("department"),
          plant: value("plant"),
          contact_number: value("contact_number"),
          email: value("email") || null,
          is_active: form.querySelector('[name="is_active"]').value === "true",
        });
        toast("Details updated.", "ok");
        reload();
      } catch (error) {
        showError(error, form);
      } finally {
        submit.disabled = false;
      }
    },
  }, [
    el("div.row", {}, [
      field("Full name", input({ name: "full_name", value: employee.full_name })),
      field("Contact number", input({ name: "contact_number", value: employee.contact_number })),
    ]),
    el("div.row", {}, [
      field("Department", input({ name: "department", value: employee.department })),
      field("Plant", input({ name: "plant", value: employee.plant })),
    ]),
    el("div.row", {}, [
      field("E-mail", input({ name: "email", type: "email", value: employee.email ?? "" })),
      field("Employment", select([["true", "Active"], ["false", "Retired"]], {
        name: "is_active", value: String(employee.is_active),
      }), "Retiring someone keeps their history but stops new examinations."),
    ]),
    submit,
  ]);
  return card("Amend details", form);
}

async function giveLogin(employee, button) {
  button.disabled = true;
  try {
    const result = await api.createEmployeeLogin(employee.id);
    // Two very different outcomes: a token to hand over in dev, a link the
    // person receives in production.
    if (result.dev_bearer_token) {
      toast(`Login created. Development token: ${result.dev_bearer_token}`, "ok");
    } else if (result.sign_in_link) {
      toast("Login created. A sign-in link has been generated for them.", "ok");
    } else {
      toast("Login created. They can sign in with their e-mail address.", "ok");
    }
  } catch (error) {
    showError(error);
  } finally {
    button.disabled = false;
  }
}

async function cancelExam(exam, mount, params) {
  const reason = window.prompt(
    "Why is this examination being cancelled?\n\n" +
    "A cancelled examination is a gap in the record, so the reason is required.");
  if (reason === null) return;
  try {
    await api.cancel(exam.id, reason);
    toast("Examination cancelled.", "ok");
    employeeDetailView(mount, params);
  } catch (error) {
    showError(error);
  }
}

/** Everything currently booked, with the ability to cancel. */
export async function scheduledView(mount) {
  render(mount, spinner("Loading booked examinations"));
  const page = await api.examinations({ status: "SCHEDULED", size: 50 });
  render(mount, card("Booked examinations",
    table([
      ["Booked for", (x) => formatDate(x.scheduled_date)],
      ["Employee", (x) => employeeName(x.employee_id)],
      ["", (x) => el("button.ghost.small", {
        text: "Cancel", onClick: async () => {
          const reason = window.prompt("Why is this examination being cancelled?");
          if (reason === null) return;
          try {
            await api.cancel(x.id, reason);
            toast("Cancelled.", "ok");
            scheduledView(mount);
          } catch (error) { showError(error); }
        },
      })],
    ], page.items, "Nothing is booked.")));
}

function employeeName(employeeId) {
  const node = el("span", { text: "…" });
  api.employee(employeeId)
    .then((e) => { node.textContent = `${e.full_name} · ${e.personal_number}`; })
    .catch(() => { node.textContent = "(could not load)"; });
  return node;
}

/** Wait for typing to stop before searching, so each keystroke is not a request. */
function debounce(fn, ms = 300) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
}
