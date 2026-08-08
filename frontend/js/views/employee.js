/**
 * What an employee sees: their own fitness status, and nothing else.
 *
 * This view answers one question — "am I cleared for work, and when is my next
 * examination?" — and answers it above the fold without a click. Everything else
 * on this screen is subordinate to that. No search, no lists, no ids: an
 * employee should never see, or be asked for, another person's data.
 */

import { api, ApiError } from "../api.js";
import { badge, card, el, fitnessKind, fitnessLabel, formatDate, render, spinner, table } from "../ui.js";

export async function myStatusView(mount) {
  render(mount, spinner("Loading your record"));

  let record;
  try {
    record = await api.myRecord();
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) {
      // A staff account signed in on this route: not an error, just not applicable.
      return render(mount, card(
        "No employee record",
        el("p.empty", {
          text: "This account is not linked to an employee record, so there is no " +
                "examination history to show.",
        }),
      ));
    }
    throw error;
  }

  const latest = record.latest_examination;
  render(mount,
    statusHeadline(record, latest),
    detailsCard(record),
    historyCard(record.id));
}

/**
 * The headline: current standing, in words, first.
 *
 * The wording is chosen so that a "you are fit" screen cannot be confused with
 * "we have nothing on file" — the two are very different for someone about to
 * start a shift.
 */
function statusHeadline(record, latest) {
  const assessed = latest && latest.status === "COMPLETED" && latest.fitness_status;

  return el("section.card.headline", {}, [
    el("p.headline-eyebrow", { text: `${record.full_name} · ${record.personal_number}` }),
    assessed
      ? el(`div.headline-status.${fitnessKind(latest.fitness_status)}`, {
          text: fitnessLabel(latest.fitness_status),
        })
      : el("div.headline-status", { text: "Not yet examined" }),
    el("p.headline-detail", {
      text: assessed
        ? `Recorded at your examination on ${formatDate(latest.exam_date)}.`
        : "You have no completed examination on file yet. Your Health Team will " +
          "arrange one.",
    }),
    latest && latest.status === "SCHEDULED"
      ? el("p.headline-note", {
          text: `An examination is booked for ${formatDate(latest.scheduled_date)}.`,
        })
      : null,
    assessed && latest.remarks
      ? el("div.headline-remarks", {}, [
          el("span.field-label", { text: "The doctor noted" }),
          el("p", { text: latest.remarks }),
        ])
      : null,
  ]);
}

function detailsCard(record) {
  const rows = [
    ["Personal number", record.personal_number],
    ["Department", record.department],
    ["Plant", record.plant],
    ["Contact number", record.contact_number],
    ["E-mail", record.email ?? "Not on file"],
  ];
  return card("My details",
    el("dl.details", {}, rows.flatMap(([label, value]) => [
      el("dt", { text: label }),
      el("dd", { text: value }),
    ])),
    el("p.field-hint", {
      text: "Something wrong here? Ask your Health Team to correct it — these " +
            "details are maintained by them.",
    }));
}

function historyCard(employeeId) {
  const body = el("div", {}, [spinner("Loading history")]);

  api.employeeHistory(employeeId, { size: 20 }).then((page) => {
    render(body, table([
      ["Examined", (x) => formatDate(x.exam_date ?? x.scheduled_date)],
      ["Outcome", (x) => x.status === "COMPLETED"
        ? badge(fitnessLabel(x.fitness_status), fitnessKind(x.fitness_status))
        : badge(x.status === "CANCELLED" ? "Cancelled" : "Booked")],
      ["Next due", (x) => formatDate(x.next_due_date)],
      ["Notes", (x) => x.remarks ?? x.cancel_reason ?? "—"],
    ], page.items, "No examinations recorded yet."));
  }).catch(() => {
    render(body, el("p.empty", { text: "Could not load your history." }));
  });

  return card("My examination history", body);
}
