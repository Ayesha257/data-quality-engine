import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext.jsx";
import { api } from "../api/client.js";
import FileDrop from "../components/FileDrop.jsx";

export default function UploadPage() {
  const { clientId } = useAuth();
  const navigate = useNavigate();

  const [file, setFile] = useState(null);
  const [sheetName, setSheetName] = useState("");
  const [targetColumn, setTargetColumn] = useState("");
  const [dateColumn, setDateColumn] = useState("");
  const [geminiKey, setGeminiKey] = useState("");
  const [interactive, setInteractive] = useState(true);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [runCompliance, setRunCompliance] = useState(false);
  const [checkHipaa, setCheckHipaa] = useState(false);
  const [checkPci, setCheckPci] = useState(false);
  const [checkGlba, setCheckGlba] = useState(false);
  const [checkSox, setCheckSox] = useState(false);
  const [checkGdpr, setCheckGdpr] = useState(false);
  const [checkCcpa, setCheckCcpa] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);

  const mlPaired = (targetColumn === "") === (dateColumn === "");

  const submit = async (e) => {
    e.preventDefault();
    setError(null);
    if (!file) {
      setError("Choose a file first.");
      return;
    }
    if (!mlPaired) {
      setError("Target column and date column must be supplied together, or left both blank.");
      return;
    }
    setSubmitting(true);
    try {
      const complianceModules = [];
      if (runCompliance) {
        if (checkHipaa) complianceModules.push("HIPAA");
        if (checkPci) complianceModules.push("PCI_DSS");
        if (checkGlba) complianceModules.push("GLBA");
        if (checkSox) complianceModules.push("SOX");
        if (checkGdpr) complianceModules.push("GDPR");
        if (checkCcpa) complianceModules.push("CCPA");
      }
      const res = await api.uploadFile({
        clientId,
        file,
        sheetName: sheetName || undefined,
        targetColumn: targetColumn || undefined,
        dateColumn: dateColumn || undefined,
        writeReport: true,
        geminiApiKey: geminiKey || undefined,
        interactive,
        includeHipaa: runCompliance && checkHipaa,
        complianceModules,
      });
      navigate(`/runs/${res.run_id}`);
    } catch (e2) {
      setError(e2.message);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="max-w-2xl mx-auto">
      <div className="mb-8">
        <h1 className="font-display text-2xl font-semibold text-mist-100">New scan</h1>
        <p className="text-sm text-mist-400 mt-1">
          Upload a dataset to profile completeness, validity, uniqueness, consistency, and more
          against <span className="font-mono text-mist-300">{clientId}</span>'s ruleset.
        </p>
      </div>

      <form onSubmit={submit} className="panel p-6 space-y-6">
        <FileDrop file={file} onSelect={setFile} />

        <div>
          <button
            type="button"
            onClick={() => setAdvancedOpen((v) => !v)}
            className="flex items-center gap-2 text-sm font-medium text-mist-200 hover:text-teal-400"
          >
            <span className={`transition-transform ${advancedOpen ? "rotate-90" : ""}`}>›</span>
            Advanced options
          </button>

          {advancedOpen && (
            <div className="mt-4 grid grid-cols-1 sm:grid-cols-2 gap-4 border-t border-ink-700 pt-4">
              <div>
                <label className="field-label">Sheet name</label>
                <input
                  className="field-input"
                  placeholder="e.g. Sheet1"
                  value={sheetName}
                  onChange={(e) => setSheetName(e.target.value)}
                />
                <p className="field-hint">Leave blank to process every visible sheet.</p>
              </div>
              <div className="sm:col-span-2 grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div>
                  <label className="field-label">Target column</label>
                  <input
                    className="field-input"
                    placeholder="e.g. churned"
                    value={targetColumn}
                    onChange={(e) => setTargetColumn(e.target.value)}
                  />
                  <p className="field-hint">For ML readiness. Pair with date column.</p>
                </div>
                <div>
                  <label className="field-label">Date column</label>
                  <input
                    className="field-input"
                    placeholder="e.g. signup_date"
                    value={dateColumn}
                    onChange={(e) => setDateColumn(e.target.value)}
                  />
                  <p className="field-hint">Time dimension for ML readiness.</p>
                </div>
              </div>
              <div className="sm:col-span-2">
                <label className="field-label">Gemini API key</label>
                <input
                  type="password"
                  className="field-input font-mono text-xs"
                  placeholder="AIzaSy... (optional, falls back to server env)"
                  value={geminiKey}
                  onChange={(e) => setGeminiKey(e.target.value)}
                  autoComplete="off"
                />
                <p className="field-hint">
                  Used only for Inspect-button explanations on the report.
                </p>
              </div>
              <div className="sm:col-span-2 space-y-3">
                <label className="flex items-start gap-3 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={runCompliance}
                    onChange={(e) => {
                      const v = e.target.checked;
                      setRunCompliance(v);
                      if (!v) {
                        setCheckHipaa(false);
                        setCheckPci(false);
                        setCheckGlba(false);
                        setCheckSox(false);
                        setCheckGdpr(false);
                        setCheckCcpa(false);
                      }
                    }}
                    className="mt-0.5 h-4 w-4 rounded border-ink-600 bg-ink-800 text-teal-500 focus:ring-teal-500/40"
                  />
                  <span className="text-sm text-mist-200">
                    Run Compliance Checks
                    <span className="block text-xs text-mist-400 mt-0.5">
                      Off by default. When off, no compliance detectors run.
                    </span>
                  </span>
                </label>
                {runCompliance && (
                  <div className="pl-7 space-y-4">
                    <div>
                      <p className="text-xs font-mono uppercase tracking-wider text-mist-400 mb-2">
                        Health Compliance
                      </p>
                      <label className="flex items-start gap-3 cursor-pointer">
                        <input
                          type="checkbox"
                          checked={checkHipaa}
                          onChange={(e) => setCheckHipaa(e.target.checked)}
                          className="mt-0.5 h-4 w-4 rounded border-ink-600 bg-ink-800 text-teal-500 focus:ring-teal-500/40"
                        />
                        <span className="text-sm text-mist-300">
                          HIPAA
                          <span className="block text-xs text-mist-400 mt-0.5">
                            Looks for protected health information such as names and phone numbers.
                          </span>
                        </span>
                      </label>
                    </div>
                    <div>
                      <p className="text-xs font-mono uppercase tracking-wider text-mist-400 mb-2">
                        Financial Compliance
                      </p>
                      <div className="space-y-2">
                        <label className="flex items-start gap-3 cursor-pointer">
                          <input
                            type="checkbox"
                            checked={checkPci}
                            onChange={(e) => setCheckPci(e.target.checked)}
                            className="mt-0.5 h-4 w-4 rounded border-ink-600 bg-ink-800 text-teal-500 focus:ring-teal-500/40"
                          />
                          <span className="text-sm text-mist-300">
                            PCI-DSS
                            <span className="block text-xs text-mist-400 mt-0.5">
                              Looks for card numbers, expiry dates, and CVV-like columns.
                            </span>
                          </span>
                        </label>
                        <label className="flex items-start gap-3 cursor-pointer">
                          <input
                            type="checkbox"
                            checked={checkGlba}
                            onChange={(e) => setCheckGlba(e.target.checked)}
                            className="mt-0.5 h-4 w-4 rounded border-ink-600 bg-ink-800 text-teal-500 focus:ring-teal-500/40"
                          />
                          <span className="text-sm text-mist-300">
                            GLBA
                            <span className="block text-xs text-mist-400 mt-0.5">
                              Looks for bank routing numbers and personal financial fields.
                            </span>
                          </span>
                        </label>
                        <label className="flex items-start gap-3 cursor-pointer">
                          <input
                            type="checkbox"
                            checked={checkSox}
                            onChange={(e) => setCheckSox(e.target.checked)}
                            className="mt-0.5 h-4 w-4 rounded border-ink-600 bg-ink-800 text-teal-500 focus:ring-teal-500/40"
                          />
                          <span className="text-sm text-mist-300">
                            SOX
                            <span className="block text-xs text-mist-400 mt-0.5">
                              Checks audit-trail columns and transaction timestamps.
                            </span>
                          </span>
                        </label>
                      </div>
                    </div>
                    <div>
                      <p className="text-xs font-mono uppercase tracking-wider text-mist-400 mb-2">
                        Privacy Compliance
                      </p>
                      <div className="space-y-2">
                        <label className="flex items-start gap-3 cursor-pointer">
                          <input
                            type="checkbox"
                            checked={checkGdpr}
                            onChange={(e) => setCheckGdpr(e.target.checked)}
                            className="mt-0.5 h-4 w-4 rounded border-ink-600 bg-ink-800 text-teal-500 focus:ring-teal-500/40"
                          />
                          <span className="text-sm text-mist-300">
                            GDPR
                            <span className="block text-xs text-mist-400 mt-0.5">
                              Scans for personal identifiers, SSNs, IPs, and geolocation data.
                            </span>
                          </span>
                        </label>
                        <label className="flex items-start gap-3 cursor-pointer">
                          <input
                            type="checkbox"
                            checked={checkCcpa}
                            onChange={(e) => setCheckCcpa(e.target.checked)}
                            className="mt-0.5 h-4 w-4 rounded border-ink-600 bg-ink-800 text-teal-500 focus:ring-teal-500/40"
                          />
                          <span className="text-sm text-mist-300">
                            CCPA
                            <span className="block text-xs text-mist-400 mt-0.5">
                              Detects California consumer personal info, identifiers, and locations.
                            </span>
                          </span>
                        </label>
                      </div>
                    </div>
                  </div>
                )}
              </div>
            </div>
          )}
        </div>

        {error && (
          <div className="text-sm text-rose-500 bg-rose-500/10 border border-rose-500/30 rounded-lg px-3 py-2">
            {error}
          </div>
        )}

        <label className="flex items-start gap-3 cursor-pointer">
          <input
            type="checkbox"
            checked={interactive}
            onChange={(e) => setInteractive(e.target.checked)}
            className="mt-0.5 h-4 w-4 rounded border-ink-600 bg-ink-800 text-teal-500 focus:ring-teal-500/40"
          />
          <span className="text-sm text-mist-300">
            Ask me to confirm the detected header row before scoring
            <span className="block text-xs text-mist-400 mt-0.5">
              Recommended for unfamiliar or irregularly-formatted files. Pauses the scan per sheet so you can
              review or override the detected header row.
            </span>
          </span>
        </label>

        <div className="flex justify-end gap-3">
          <button type="submit" className="btn-primary" disabled={submitting}>
            {submitting ? "Uploading…" : "Start scan"}
          </button>
        </div>
      </form>
    </div>
  );
}