import { useEffect, useState } from "react";
import { useAuth } from "../context/AuthContext.jsx";
import { api } from "../api/client.js";
import RuleBuilder from "../components/RuleBuilder.jsx";

export default function RulesPage() {
  const { clientId } = useAuth();
  const [active, setActive] = useState(null);
  const [loadError, setLoadError] = useState(null);

  const [draft, setDraft] = useState("");
  const [mode, setMode] = useState("form"); // "form" | "yaml"
  const [dryRun, setDryRun] = useState(null);
  const [busy, setBusy] = useState(false);
  const [saveResult, setSaveResult] = useState(null);
  const [actionError, setActionError] = useState(null);

  useEffect(() => {
    api
      .getClientRules(clientId)
      .then(setActive)
      .catch((e) => setLoadError(e.message));
  }, [clientId]);

  const runDryRun = async () => {
    setBusy(true);
    setActionError(null);
    setSaveResult(null);
    try {
      const res = await api.dryRunRules(clientId, draft);
      setDryRun(res);
    } catch (e) {
      setActionError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const save = async () => {
    setBusy(true);
    setActionError(null);
    try {
      const res = await api.saveClientRules(clientId, draft);
      setSaveResult(res);
      const refreshed = await api.getClientRules(clientId);
      setActive(refreshed);
    } catch (e) {
      setActionError(e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-6">
      <div>
        <h1 className="font-display text-2xl font-semibold text-mist-100">Rules</h1>
        <p className="text-sm text-mist-400 mt-1">
          Base rules merged with {clientId}'s highest-version override, if any.
        </p>
      </div>

      <div className="panel p-6">
        <div className="flex items-center justify-between mb-4">
          <h2 className="font-display font-semibold text-mist-100">Active ruleset</h2>
          {active && <span className="badge bg-ink-800 border border-ink-600 text-mist-300">v{active.version}</span>}
        </div>
        {loadError && <p className="text-sm text-rose-500">{loadError}</p>}
        {!active && !loadError && <p className="text-sm text-mist-400">Loading…</p>}
        {active && (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <p className="field-label">Thresholds</p>
              <pre className="font-mono text-xs text-mist-300 bg-ink-800 rounded-lg p-3 overflow-auto max-h-64">
                {JSON.stringify(active.thresholds, null, 2)}
              </pre>
            </div>
            <div>
              <p className="field-label">Business rules ({active.business_rules?.length ?? 0})</p>
              <pre className="font-mono text-xs text-mist-300 bg-ink-800 rounded-lg p-3 overflow-auto max-h-64">
                {JSON.stringify(active.business_rules, null, 2)}
              </pre>
            </div>
          </div>
        )}
      </div>

      <div className="panel p-6">
        <div className="flex items-center justify-between mb-1">
          <h2 className="font-display font-semibold text-mist-100">Draft a new version</h2>
          <div className="flex items-center gap-1 rounded-lg border border-ink-600 p-1 bg-ink-800/60">
            <button
              type="button"
              onClick={() => setMode("form")}
              className={`text-xs px-3 py-1 rounded-md transition ${
                mode === "form" ? "bg-teal-500 text-ink-950 font-semibold" : "text-mist-400 hover:text-mist-200"
              }`}
            >
              Build rules
            </button>
            <button
              type="button"
              onClick={() => setMode("yaml")}
              className={`text-xs px-3 py-1 rounded-md transition ${
                mode === "yaml" ? "bg-teal-500 text-ink-950 font-semibold" : "text-mist-400 hover:text-mist-200"
              }`}
            >
              Raw YAML
            </button>
          </div>
        </div>
        <p className="text-sm text-mist-400 mb-4">
          {mode === "form"
            ? "Fill in one line per rule — the underlying rule file is generated automatically."
            : "Paste raw YAML directly."}
          {" "}Dry-run to validate, then save as{" "}
          <code className="text-mist-300">
            rules_v{active?.version != null ? Number(active.version) + 1 : "1"}
          </code>
          .
        </p>

        {mode === "form" ? (
          <RuleBuilder onYamlChange={setDraft} />
        ) : (
          <textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            spellCheck={false}
            placeholder={"thresholds:\n  completeness_min: 0.9\nbusiness_rules:\n  - name: positive_amount\n    column: Amount\n    rule: \">= 0\""}
            className="field-input font-mono text-xs h-64 resize-y leading-relaxed"
          />
        )}
        <div className="flex items-center gap-3 mt-4">
          <button className="btn-secondary" onClick={runDryRun} disabled={busy || !draft.trim()}>
            {busy ? "Working…" : "Dry run"}
          </button>
          <button
            className="btn-primary"
            onClick={save}
            disabled={busy || !draft.trim() || (dryRun && !dryRun.valid)}
          >
            Save new version
          </button>
        </div>

        {actionError && (
          <div className="mt-4 text-sm text-rose-500 bg-rose-500/10 border border-rose-500/30 rounded-lg px-3 py-2">
            {actionError}
          </div>
        )}

        {dryRun && (
          <div
            className={`mt-4 text-sm rounded-lg px-3 py-2 border ${
              dryRun.valid
                ? "text-teal-400 bg-teal-500/10 border-teal-500/30"
                : "text-rose-500 bg-rose-500/10 border-rose-500/30"
            }`}
          >
            {dryRun.valid ? (
              <>Valid — {dryRun.thresholds} threshold key(s), {dryRun.business_rules} business rule(s).</>
            ) : (
              <>Invalid: {dryRun.error}</>
            )}
          </div>
        )}

        {saveResult && (
          <div className="mt-4 text-sm text-teal-400 bg-teal-500/10 border border-teal-500/30 rounded-lg px-3 py-2">
            Saved as version {saveResult.version} → <span className="font-mono">{saveResult.path}</span>
          </div>
        )}
      </div>
    </div>
  );
}
