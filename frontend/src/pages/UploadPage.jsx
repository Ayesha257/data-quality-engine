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
  const [writeReport, setWriteReport] = useState(true);
  const [geminiKey, setGeminiKey] = useState("");
  const [advancedOpen, setAdvancedOpen] = useState(false);
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
      const res = await api.uploadFile({
        clientId,
        file,
        sheetName: sheetName || undefined,
        targetColumn: targetColumn || undefined,
        dateColumn: dateColumn || undefined,
        writeReport,
        geminiApiKey: geminiKey || undefined,
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
                  placeholder="all visible sheets"
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
                </div>
                <div>
                  <label className="field-label">Date column</label>
                  <input
                    className="field-input"
                    placeholder="e.g. order_date"
                    value={dateColumn}
                    onChange={(e) => setDateColumn(e.target.value)}
                  />
                </div>
                <p className="field-hint sm:col-span-2 -mt-2">
                  Supply both to enable ML-readiness scoring (leakage, temporal, target checks).
                </p>
              </div>
              <div className="sm:col-span-2">
                <label className="field-label">Gemini API key override</label>
                <input
                  className="field-input font-mono"
                  type="password"
                  placeholder="uses server default if left blank"
                  value={geminiKey}
                  onChange={(e) => setGeminiKey(e.target.value)}
                />
              </div>
              <label className="sm:col-span-2 flex items-center gap-2.5 cursor-pointer select-none">
                <input
                  type="checkbox"
                  checked={writeReport}
                  onChange={(e) => setWriteReport(e.target.checked)}
                  className="w-4 h-4 rounded border-ink-600 bg-ink-800 text-teal-500 focus:ring-teal-500 focus:ring-offset-0"
                />
                <span className="text-sm text-mist-200">Generate the AI-enhanced HTML report</span>
              </label>
            </div>
          )}
        </div>

        {error && (
          <div className="text-sm text-rose-500 bg-rose-500/10 border border-rose-500/30 rounded-lg px-3 py-2">
            {error}
          </div>
        )}

        <div className="flex justify-end gap-3">
          <button type="submit" className="btn-primary" disabled={submitting}>
            {submitting ? "Uploading…" : "Start scan"}
          </button>
        </div>
      </form>
    </div>
  );
}
