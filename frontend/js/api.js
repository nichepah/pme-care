/**
 * The single place that talks to the API.
 *
 * Every call goes through `request()`, so the bearer token, the error envelope
 * and the "your session ended" case are handled once rather than in each view.
 */

import { clearSession, getToken } from "./session.js";

/** Base URL of the API. Same-origin when the backend serves this app. */
export const API_BASE =
  window.PME_API_BASE ?? `${window.location.protocol}//${window.location.hostname}:8080/api/v1`;

/** Raised for any non-2xx response, carrying the parsed error envelope. */
export class ApiError extends Error {
  constructor(status, body) {
    const envelope = body?.error ?? {};
    super(envelope.message || `Request failed (${status})`);
    this.name = "ApiError";
    this.status = status;
    this.code = envelope.code ?? "UNKNOWN";
    /** Per-field problems, e.g. [{field: "remarks", issue: "required"}]. */
    this.details = envelope.details ?? [];
    this.requestId = envelope.request_id ?? null;
  }

  /** The issue reported for one field, if the API named it. */
  issueFor(field) {
    return this.details.find((d) => d.field === field)?.issue ?? null;
  }
}

async function request(method, path, body) {
  const token = getToken();
  const response = await fetch(API_BASE + path, {
    method,
    headers: {
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(body === undefined ? {} : { "Content-Type": "application/json" }),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });

  if (response.status === 204) return null;

  const payload = await response.json().catch(() => ({}));
  if (response.ok) return payload;

  // A 401 means the token is gone, expired, or the account was deactivated.
  // Dropping the session here means no view has to think about it.
  if (response.status === 401) {
    clearSession();
    window.location.hash = "#/login";
  }
  throw new ApiError(response.status, payload);
}

/** Build a query string, omitting empty values so filters can be passed blank. */
export function query(params) {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== null && value !== undefined && value !== "") search.set(key, value);
  }
  const string = search.toString();
  return string ? `?${string}` : "";
}

export const api = {
  get: (path) => request("GET", path),
  post: (path, body) => request("POST", path, body ?? {}),
  patch: (path, body) => request("PATCH", path, body),
  del: (path) => request("DELETE", path),

  // Named calls, so a view never hand-builds a URL and a route rename is one edit.
  me: () => request("GET", "/me"),
  myRecord: () => request("GET", "/employees/me"),
  /** An employee's examination history. Named for the record, not the caller —
   *  staff read other people's; an employee only ever gets their own. */
  employeeHistory: (employeeId, params = {}) =>
    request("GET", `/employees/${employeeId}/examinations${query(params)}`),

  employees: (params = {}) => request("GET", `/employees${query(params)}`),
  employee: (id) => request("GET", `/employees/${id}`),
  registerEmployee: (body) => request("POST", "/employees", body),
  updateEmployee: (id, body) => request("PATCH", `/employees/${id}`, body),
  createEmployeeLogin: (id) => request("POST", `/employees/${id}/login`, {}),
  dueEmployees: (params = {}) => request("GET", `/employees/due${query(params)}`),

  examinations: (params = {}) => request("GET", `/examinations${query(params)}`),
  examination: (id) => request("GET", `/examinations/${id}`),
  schedule: (body) => request("POST", "/examinations", body),
  complete: (id, body) => request("POST", `/examinations/${id}/complete`, body),
  cancel: (id, reason) => request("POST", `/examinations/${id}/cancel`, { reason }),

  users: (params = {}) => request("GET", `/users${query(params)}`),
  createUser: (body) => request("POST", "/users", body),
  updateUser: (id, body) => request("PATCH", `/users/${id}`, body),
  auditLogs: (params = {}) => request("GET", `/audit-logs${query(params)}`),
};
