import axios from "axios";

// In dev, Vite's proxy (vite.config.js) forwards /api/* to the FastAPI
// backend, so no CORS setup is required at all. For a production build,
// set VITE_API_BASE_URL to the deployed API's origin.
const BASE_URL = import.meta.env.VITE_API_BASE_URL || "/api";

export const STORAGE_KEYS = {
  token: "dqe_token",
  clientId: "dqe_client_id",
  email: "dqe_email",
};

const client = axios.create({ baseURL: BASE_URL });

client.interceptors.request.use((config) => {
  const token = localStorage.getItem(STORAGE_KEYS.token);
  if (token) config.headers["Authorization"] = `Bearer ${token}`;
  return config;
});

client.interceptors.response.use(
  (res) => res,
  (err) => {
    // Normalize FastAPI's {"detail": "..."} into a plain Error message so
    // every page can just do `catch (e) { setError(e.message) }`.
    const detail = err?.response?.data?.detail;
    const message = Array.isArray(detail)
      ? detail.map((d) => d.msg || JSON.stringify(d)).join("; ")
      : detail || err.message || "Unexpected error.";
    return Promise.reject(new Error(message));
  }
);

// ---------------------------------------------------------------------
// Endpoint bindings — maps 1:1 to routes in
// data_quality_engine/phase2/api/routes.py + auth_routes.py.
// ---------------------------------------------------------------------

export const api = {
  health: () => client.get("/health").then((r) => r.data),

  // --- auth --------------------------------------------------------
  register: ({ email, password, fullName }) =>
    client
      .post("/v1/auth/register", { email, password, full_name: fullName || null })
      .then((r) => r.data),

  login: ({ email, password }) =>
    client.post("/v1/auth/login", { email, password }).then((r) => r.data),

  me: () => client.get("/v1/auth/me").then((r) => r.data),

  // --- data quality pipeline ----------------------------------------
  uploadFile: ({ clientId, file, sheetName, targetColumn, dateColumn, writeReport, geminiApiKey }) => {
    const form = new FormData();
    form.append("file", file);
    const params = { client_id: clientId, write_report: writeReport };
    if (sheetName) params.sheet_name = sheetName;
    if (targetColumn) params.target_column = targetColumn;
    if (dateColumn) params.date_column = dateColumn;
    if (geminiApiKey) params.gemini_api_key = geminiApiKey;
    return client
      .post("/v1/files/upload", form, {
        params,
        headers: { "Content-Type": "multipart/form-data" },
      })
      .then((r) => r.data);
  },

  getRunStatus: (runId) => client.get(`/v1/runs/${runId}/status`).then((r) => r.data),

  getRunResults: (runId) => client.get(`/v1/runs/${runId}/results`).then((r) => r.data),

  // A plain <a href> can't attach the Authorization header, so we fetch
  // the HTML report as an authenticated blob and hand back an object URL.
  fetchReportBlobUrl: (runId, sheetName) => {
    const params = sheetName ? { sheet_name: sheetName } : {};
    return client
      .get(`/v1/runs/${runId}/report`, { params, responseType: "blob" })
      .then((r) => URL.createObjectURL(r.data));
  },

  // Same pattern as fetchReportBlobUrl but for the real PDF file (not the
  // browser's print dialog) -- a genuine downloadable application/pdf blob.
  fetchReportPdfBlobUrl: (runId, sheetName) => {
    const params = sheetName ? { sheet_name: sheetName } : {};
    return client
      .get(`/v1/runs/${runId}/report/pdf`, { params, responseType: "blob" })
      .then((r) => URL.createObjectURL(r.data));
  },

  listRuns: (clientId) =>
    client.get(`/v1/clients/${encodeURIComponent(clientId)}/runs`).then((r) => r.data.runs),

  getClientRules: (clientId) =>
    client.get(`/v1/clients/${encodeURIComponent(clientId)}/rules`).then((r) => r.data),

  dryRunRules: (clientId, rulesYaml) =>
    client
      .post(`/v1/clients/${encodeURIComponent(clientId)}/rules/dry-run`, { rules_yaml: rulesYaml })
      .then((r) => r.data),

  saveClientRules: (clientId, rulesYaml) =>
    client
      .post(`/v1/clients/${encodeURIComponent(clientId)}/rules`, { rules_yaml: rulesYaml })
      .then((r) => r.data),
};

export default client;
