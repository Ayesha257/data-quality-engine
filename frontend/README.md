# DQE Console

A React + Vite + Tailwind frontend for the Data Quality Engine's Phase 2 (M4)
REST API — login, upload a dataset, watch it get scored, browse results and the
generated report, and manage per-client rules.

## 1. Apply the backend changes first

This frontend **will not connect** until you apply the two small backend
changes in `../backend_patches/README.md` (CORS is non-negotiable — every
request is blocked client-side without it; the list-runs endpoint is needed for
the Runs page). Do that first, then come back here.

## 2. Run the backend

```bash
cd data-quality-engine
pip install -r requirements.txt
# DQE_API_KEYS must be set in .env, e.g.:
#   DQE_API_KEYS=dqe_yourkey:acme_corp,dqe_admin_key:*
#   DQE_CORS_ORIGINS=http://localhost:5173
uvicorn data_quality_engine.phase2.api.app:app --reload
```

## 3. Run the frontend

```bash
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173`. In dev, Vite proxies every `/api/*` call to
`http://127.0.0.1:8000` (see `vite.config.js`) — so you never hit a CORS
problem locally even before applying the backend patch, though the patch is
still required for any deployed environment where frontend and backend aren't
served through the same proxy.

Log in with a `client_id` and one of the `X-API-Key` values from your
`DQE_API_KEYS`. An admin key (`*` scope) works with any client ID you type.

## 4. Production build

```bash
npm run build   # outputs static/dist
```

Set `VITE_API_BASE_URL` (see `.env.example`) to your deployed API's origin
before building, and make sure `DQE_CORS_ORIGINS` on the backend includes
wherever you end up hosting the built frontend.

## What's here

```
src/
  api/client.js          Every backend call lives here — one function per endpoint.
  context/AuthContext.jsx "Login" = verified {api_key, client_id}, kept in localStorage.
  components/             Layout/nav, ScoreDial (gauge), StatusBadge, DimensionBars, FileDrop.
  pages/
    LoginPage.jsx          API key + client ID, verified against GET /v1/clients/{id}/rules.
    UploadPage.jsx         POST /v1/files/upload (+ advanced: sheet/target/date columns).
    RunsPage.jsx            GET /v1/clients/{id}/runs — history list (needs the backend patch).
    RunDetailPage.jsx      Polls GET /v1/runs/{id}/status every 2.5s, then loads
                           /results and lets you open each sheet's /report inline.
    RulesPage.jsx          GET/POST rules + dry-run, against /v1/clients/{id}/rules*.
```

## Notes on the auth model

The backend authenticates with a static API key bound to one `client_id` (see
`phase2/api/auth.py`) — there's no username/password or session concept on the
server. The "login" screen here is the honest frontend counterpart to that: you
enter a client ID + API key, the app verifies it against a real endpoint, and
holds it for the browser session. See `../backend_patches/README.md` §4 if you
want to build toward real multi-user accounts later.
