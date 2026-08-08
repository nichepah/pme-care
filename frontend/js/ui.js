/**
 * Rendering helpers shared by every view.
 *
 * All text reaches the DOM through `el()`/`text()`, which use textContent, so
 * employee names and remarks cannot inject markup. There is no innerHTML with
 * interpolated data anywhere in this app.
 */

/**
 * Build an element.
 *
 * @param spec e.g. "button.primary" or "td"
 * @param props textContent via `text`, listeners via `onClick`, everything else
 *              set as an attribute (or a property for form values)
 * @param children nodes or strings, nulls skipped
 */
export function el(spec, props = {}, children = []) {
  const [tag, ...classes] = spec.split(".");
  const node = document.createElement(tag || "div");
  if (classes.length) node.className = classes.join(" ");

  for (const [key, value] of Object.entries(props)) {
    if (value === null || value === undefined) continue;
    if (key === "text") node.textContent = String(value);
    else if (key === "onClick") node.addEventListener("click", value);
    else if (key === "onSubmit") node.addEventListener("submit", value);
    else if (key === "onInput") node.addEventListener("input", value);
    else if (key in node && key !== "list") node[key] = value;
    else node.setAttribute(key, String(value));
  }

  for (const child of [children].flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(typeof child === "string" ? document.createTextNode(child) : child);
  }
  return node;
}

export const text = (value) => document.createTextNode(String(value ?? ""));

/** Replace an element's contents. */
export function render(target, ...children) {
  target.replaceChildren(...children.flat().filter(Boolean));
}

/** A labelled form field. `input` is the control element. */
export function field(label, input, hint = null) {
  return el("label.field", {}, [
    el("span.field-label", { text: label }),
    input,
    hint ? el("span.field-hint", { text: hint }) : null,
  ]);
}

export function input(props = {}) {
  return el("input", { type: "text", ...props });
}

export function select(options, props = {}) {
  return el(
    "select",
    props,
    options.map(([value, label]) => el("option", { value, text: label })),
  );
}

/**
 * A table. `columns` is [[heading, cellFn], ...]; cellFn returns a string or node.
 * `empty` is what to say when there are no rows — never a blank area, which
 * reads as "still loading".
 */
export function table(columns, rows, empty = "Nothing to show.") {
  if (!rows.length) return el("p.empty", { text: empty });
  return el("div.table-scroll", {}, [
    el("table", {}, [
      el("thead", {}, [
        el("tr", {}, columns.map(([heading]) => el("th", { text: heading }))),
      ]),
      el(
        "tbody",
        {},
        rows.map((row) =>
          el("tr", {}, columns.map(([, cell]) => {
            const value = cell(row);
            return el("td", {}, [typeof value === "object" && value !== null ? value : text(value)]);
          })),
        ),
      ),
    ]),
  ]);
}

/** A short-lived message in the corner. Errors stay longer, being worth reading. */
export function toast(message, kind = "info") {
  let host = document.getElementById("toasts");
  if (!host) {
    host = el("div", { id: "toasts" });
    document.body.append(host);
  }
  const node = el(`div.toast.${kind}`, { text: message, role: "status" });
  host.append(node);
  setTimeout(() => node.remove(), kind === "error" ? 8000 : 3500);
}

/**
 * Report a failure to the user.
 *
 * Field-level problems are shown as such; anything else becomes a toast. The
 * request id is included when present, because it is what makes a reported
 * problem findable in the logs.
 */
export function showError(error, formNode = null) {
  if (formNode && error.details?.length) {
    for (const { field: name, issue } of error.details) {
      const control = formNode.querySelector(`[name="${name}"]`);
      if (control) {
        control.classList.add("invalid");
        control.closest(".field")?.append(el("span.field-error", { text: issue }));
      }
    }
  }
  const suffix = error.requestId ? ` (ref ${error.requestId})` : "";
  toast(`${error.message}${suffix}`, "error");
}

/** Clear previous validation marks before resubmitting. */
export function clearErrors(formNode) {
  formNode.querySelectorAll(".invalid").forEach((n) => n.classList.remove("invalid"));
  formNode.querySelectorAll(".field-error").forEach((n) => n.remove());
}

export function card(title, ...children) {
  return el("section.card", {}, [title ? el("h2", { text: title }) : null, ...children]);
}

/** A big number with a label — the top of a dashboard. */
export function stat(label, value, kind = "") {
  return el(`div.stat.${kind}`.trimEnd("."), {}, [
    el("div.stat-value", { text: value }),
    el("div.stat-label", { text: label }),
  ]);
}

export function badge(label, kind = "") {
  return el(`span.badge.${kind}`.replace(/\.$/, ""), { text: label ?? "—" });
}

/** Placeholder shown while a view's data is in flight. */
export function spinner(what = "Loading") {
  return el("p.empty", { text: `${what}…` });
}

/** ISO date -> "9 Aug 2026". Dates here are calendar dates, never times. */
export function formatDate(iso) {
  if (!iso) return "—";
  const [y, m, d] = iso.slice(0, 10).split("-").map(Number);
  const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  return `${d} ${months[m - 1]} ${y}`;
}

/** Today, as the API wants it. */
export function today() {
  const now = new Date();
  const pad = (n) => String(n).padStart(2, "0");
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
}

/** Human wording for a fitness outcome. */
export function fitnessLabel(status) {
  return {
    FIT: "Fit",
    TEMPORARILY_UNFIT: "Temporarily unfit",
    UNFIT: "Unfit",
  }[status] ?? "Not assessed";
}

export function fitnessKind(status) {
  return { FIT: "ok", TEMPORARILY_UNFIT: "warn", UNFIT: "bad" }[status] ?? "";
}

/** Pager: "31–40 of 87" with prev/next. `onPage` receives the new page number. */
export function pager(page, onPage) {
  const { total, size, page: current } = page;
  const pages = Math.max(1, Math.ceil(total / size));
  if (total === 0) return null;
  const first = (current - 1) * size + 1;
  const last = Math.min(total, current * size);
  return el("div.pager", {}, [
    el("span.pager-info", { text: `${first}–${last} of ${total}` }),
    el("button.ghost", {
      text: "Previous", disabled: current <= 1, onClick: () => onPage(current - 1),
    }),
    el("button.ghost", {
      text: "Next", disabled: current >= pages, onClick: () => onPage(current + 1),
    }),
  ]);
}
