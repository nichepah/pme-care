/**
 * What a doctor sees: today's examinations, and the form to record one.
 *
 * The whole view is built around one repeated action — open a booked
 * examination, read the person's history, record a decision — so the worklist
 * leads directly into the form rather than making the doctor copy an id.
 *
 * The previous history is shown *beside* the form on purpose: a fitness decision
 * is a judgement about a trend, and asking a doctor to navigate away to see the
 * last reading is how the trend gets ignored.
 */

import { api } from "../api.js";
import { navigate } from "../router.js";
import {
  badge, card, clearErrors, el, field, fitnessKind, fitnessLabel, formatDate,
  input, render, select, showError, spinner, table, toast,
} from "../ui.js";

/** The worklist: everything booked, soonest first. */
export async function doctorWorklistView(mount) {
  render(mount, spinner("Loading the worklist"));
  const page = await api.examinations({ status: "SCHEDULED", size: 50 });

  const overdueFirst = page.items;
  render(mount, card(
    "Examinations to do",
    overdueFirst.length
      ? el("p.field-hint", { text: `${page.total} booked. Soonest first.` })
      : null,
    table([
      ["Booked for", (x) => formatDate(x.scheduled_date)],
      ["Employee", (x) => employeeCell(x.employee_id)],
      ["Assigned", (x) => x.doctor_user_id ? badge("To a doctor") : badge("Unassigned", "warn")],
      ["", (x) => el("button.primary.small", {
        text: "Examine", onClick: () => navigate(`/examine?id=${x.id}`),
      })],
    ], overdueFirst, "Nothing is booked. The Health Team schedules examinations."),
  ));
}

/**
 * Resolve an employee's name lazily.
 *
 * The worklist returns employee ids, not names, and there is no batch lookup —
 * so each row fetches its own. Acceptable for a worklist of tens; if it ever
 * grows to hundreds, the fix belongs in the API (embed the name in the
 * examination response) rather than in more requests from here.
 */
function employeeCell(employeeId) {
  const node = el("span", { text: "…" });
  api.employee(employeeId)
    .then((e) => { node.textContent = `${e.full_name} · ${e.personal_number}`; })
    .catch(() => { node.textContent = "(could not load)"; });
  return node;
}

/** The examination form for one booked examination. */
export async function examineView(mount, params) {
  if (!params.id) return navigate("/worklist");

  render(mount, spinner("Loading the examination"));
  const exam = await api.examination(params.id);
  const employee = await api.employee(exam.employee_id);

  if (exam.status !== "SCHEDULED") {
    return render(mount, card(
      "Already recorded",
      el("p.empty", {
        text: `This examination is ${exam.status.toLowerCase()} and cannot be changed. ` +
              "A new examination has to be booked instead.",
      }),
      el("button.ghost", { text: "Back to the worklist", onClick: () => navigate("/worklist") }),
    ));
  }

  render(mount,
    el("div.two-column", {}, [
      examinationForm(exam, employee),
      priorHistory(employee),
    ]));
}

function examinationForm(exam, employee) {
  const fitness = select([
    ["FIT", "Fit for work"],
    ["TEMPORARILY_UNFIT", "Temporarily unfit"],
    ["UNFIT", "Unfit"],
  ], { name: "fitness_status" });

  const remarks = el("textarea", { name: "remarks", rows: 4 });
  const remarksField = field("Remarks", remarks,
    "Required for any outcome other than Fit — this is the record of why.");

  // Show the requirement before submitting rather than after being refused.
  const syncRemarks = () => {
    const needed = fitness.value !== "FIT";
    remarksField.classList.toggle("required", needed);
    remarks.placeholder = needed
      ? "Explain the decision — required."
      : "Optional.";
  };
  fitness.addEventListener("change", syncRemarks);
  syncRemarks();

  const recall = input({ name: "next_due_date", type: "date" });
  const submit = el("button.primary", { type: "submit", text: "Record this examination" });

  const form = el("form", {
    onSubmit: async (event) => {
      event.preventDefault();
      clearErrors(form);
      submit.disabled = true;
      const numberOrNull = (name) => {
        const raw = form.querySelector(`[name="${name}"]`).value.trim();
        return raw === "" ? null : Number(raw);
      };
      try {
        await api.complete(exam.id, {
          fitness_status: fitness.value,
          bp_systolic: numberOrNull("bp_systolic"),
          bp_diastolic: numberOrNull("bp_diastolic"),
          height_cm: numberOrNull("height_cm"),
          weight_kg: numberOrNull("weight_kg"),
          remarks: remarks.value.trim() || null,
          next_due_date: recall.value || null,
        });
        toast(`Recorded for ${employee.full_name}.`, "ok");
        navigate("/worklist");
      } catch (error) {
        showError(error, form);
      } finally {
        submit.disabled = false;
      }
    },
  }, [
    el("div.row", {}, [
      field("Blood pressure — systolic", input({ name: "bp_systolic", type: "number", placeholder: "120", min: 40, max: 300 })),
      field("diastolic", input({ name: "bp_diastolic", type: "number", placeholder: "80", min: 20, max: 200 })),
    ]),
    el("div.row", {}, [
      field("Height (cm)", input({ name: "height_cm", type: "number", step: "0.1", placeholder: "172" })),
      field("Weight (kg)", input({ name: "weight_kg", type: "number", step: "0.1", placeholder: "70" })),
    ]),
    field("Decision", fitness),
    remarksField,
    field("Recall date", recall,
      "Leave blank to use the standard interval for the decision above."),
    submit,
  ]);

  return card(null,
    el("p.headline-eyebrow", { text: `Booked for ${formatDate(exam.scheduled_date)}` }),
    el("h2", { text: employee.full_name }),
    el("p.field-hint", {
      text: `${employee.personal_number} · ${employee.department} · ${employee.plant}`,
    }),
    form);
}

/** Previous examinations, so the decision is made against the trend. */
function priorHistory(employee) {
  const body = el("div", {}, [spinner("Loading previous examinations")]);

  api.employeeHistory(employee.id, { size: 10 }).then((page) => {
    const completed = page.items.filter((x) => x.status === "COMPLETED");
    render(body, table([
      ["Date", (x) => formatDate(x.exam_date)],
      ["Outcome", (x) => badge(fitnessLabel(x.fitness_status), fitnessKind(x.fitness_status))],
      ["BP", (x) => x.bp_systolic ? `${x.bp_systolic}/${x.bp_diastolic}` : "—"],
      ["Weight", (x) => x.weight_kg ? `${x.weight_kg} kg` : "—"],
      ["Remarks", (x) => x.remarks ?? "—"],
    ], completed, "No previous examinations — this is their first."));
  }).catch(() => {
    render(body, el("p.empty", { text: "Could not load previous examinations." }));
  });

  return card("Previous examinations", body);
}
