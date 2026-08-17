import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useAuth } from "../context/AuthContext.jsx";
import { api } from "../api/client.js";

// key -> display label. "hipaa" is the only supported module today; this
// stays a plain lookup so a second module is just one more entry, same
// as ProfilePage's COMPLIANCE_MODULES list.
const COMPLIANCE_MODULES = [
  {
    key: "hipaa",
    label: "HIPAA",
    description: "Protected health information (PHI) exposure findings across your scans.",
  },
];

export default function CompliancePage() {
  const { module } = useParams();
  const { clientId } = useAuth();
  const activeModuleKey = module || "hipaa";
  const activeModule =
    COMPLIANCE_MODULES.find((m) => m.key === activeModuleKey) || COMPLIANCE_MODULES[0];
  const label = activeModule.label;

  const [runs, setRuns] = useState(null);
  const [error, setError] = useState(null);
  const [selectedRunId, setSelectedRunId] = useState(null);
  const [reportUrl, setReportUrl] = useState(null);
  const [reportLoading, setReportLoading] = useState(false);
  const [reportError, setReportError] = useState(null);

  useEffect(() => {
    api
      .listRuns(clientId)
      .then((all) => {
        const filtered = all.filter(
          (r) => r.status === "completed" && r.has_compliance_report !== false
        );
        setRuns(filtered);
      })
      .catch((e) => setError(e.message));
  }, [clientId]);

  useEffect(() => () => reportUrl && URL.revokeObjectURL(reportUrl), [reportUrl]);

  const viewReport = async (runId) => {
    setSelectedRunId(runId);
    setReportLoading(true);
    setReportError(null);
    try {
      if (reportUrl) URL.revokeObjectURL(reportUrl);
      const url = await api.fetchComplianceReportBlobUrl(runId);
      setReportUrl(url);
    } catch (e) {
      setReportError(e.message);
      setReportUrl(null);
      // Auto-remove run from sidebar if compliance report is not available
      setRuns((prev) => (prev ? prev.filter((r) => r.run_id !== runId) : prev));
    } finally {
      setReportLoading(false);
    }
  };

  const deleteRun = async (e, runId, fileName) => {
    e.stopPropagation();
    if (
      !window.confirm(
        `Are you sure you want to delete the run for "${fileName}"? This will permanently remove its reports.`
      )
    ) {
      return;
    }
    try {
      await api.deleteRun(runId);
      setRuns((prev) => (prev ? prev.filter((r) => r.run_id !== runId) : []));
      if (selectedRunId === runId) {
        setSelectedRunId(null);
        if (reportUrl) URL.revokeObjectURL(reportUrl);
        setReportUrl(null);
        setReportError(null);
      }
    } catch (err) {
      setError(`Failed to delete run: ${err.message}`);
    }
  };

  const selectedRun = runs?.find((r) => r.run_id === selectedRunId);

  const printPdf = () => {
    const iframe = document.getElementById("complianceReportIframe");
    if (iframe && iframe.contentWindow) {
      iframe.contentWindow.focus();
      iframe.contentWindow.print();
    } else {
      window.print();
    }
  };

  return (
    <div>
      <div className="mb-6">
        <h1 className="font-display text-2xl font-semibold text-mist-100">
          Compliance
        </h1>
        <p className="text-sm text-mist-400 mt-1">
          Standalone compliance reports for your completed scans.
        </p>
      </div>

      <div className="flex gap-2 border-b border-ink-700 pb-4 mb-6">
        {COMPLIANCE_MODULES.map((mod) => (
          <Link
            key={mod.key}
            to={`/compliance/${mod.key}`}
            className={`px-3 py-1.5 rounded-lg text-sm font-medium transition-colors ${
              activeModuleKey === mod.key
                ? "bg-teal-500/10 text-teal-400 border border-teal-500/30"
                : "bg-ink-800 text-mist-300 hover:text-mist-100 border border-ink-700"
            }`}
          >
            {mod.label}
          </Link>
        ))}
      </div>

      {error && (
        <div className="panel p-4 mb-4 text-sm text-rose-500 border-rose-500/30">{error}</div>
      )}

      {runs === null && !error && (
        <div className="panel p-5 space-y-3">
          {[...Array(3)].map((_, i) => (
            <div key={i} className="h-10 rounded-lg bg-ink-800 animate-pulseSoft" />
          ))}
        </div>
      )}

      {runs && runs.length === 0 && (
        <div className="panel p-12 text-center">
          <p className="text-mist-300 font-medium">No compliance reports available</p>
          <p className="text-sm text-mist-400 mt-1">
            Run a scan first, then come back here for its {label} compliance report.
          </p>
          <Link to="/upload" className="btn-primary mt-4 inline-flex">
            Start a scan
          </Link>
        </div>
      )}

      {runs && runs.length > 0 && (
        <div className="grid gap-6 lg:grid-cols-[280px_1fr]">
          <div className="panel overflow-hidden self-start">
            <div className="px-4 py-2.5 text-xs font-mono uppercase tracking-wider text-mist-400 border-b border-ink-800 bg-ink-900/40">
              Files with Compliance
            </div>
            {runs.map((run) => (
              <div
                key={run.run_id}
                onClick={() => viewReport(run.run_id)}
                className={`w-full flex items-center justify-between px-4 py-3 border-b border-ink-800 last:border-0 cursor-pointer transition-colors ${
                  selectedRunId === run.run_id
                    ? "bg-ink-800 text-teal-400"
                    : "text-mist-200 hover:bg-ink-800/60"
                }`}
              >
                <div className="min-w-0 flex-1">
                  <div className="font-mono text-sm truncate">{run.file_name}</div>
                  <div className="text-xs text-mist-400 mt-0.5">
                    {new Date(run.started_at).toLocaleString()}
                  </div>
                </div>
                <button
                  type="button"
                  onClick={(e) => deleteRun(e, run.run_id, run.file_name)}
                  className="ml-2 text-xs text-rose-400 hover:text-rose-300 p-1 rounded hover:bg-rose-500/10"
                  title="Delete run"
                >
                  Delete
                </button>
              </div>
            ))}
          </div>

          <div className="panel min-h-[520px] flex flex-col overflow-hidden">
            {!selectedRunId && (
              <div className="flex-1 flex items-center justify-center text-sm text-mist-400 p-8 text-center">
                Select a scan on the left to view its {label} compliance report.
              </div>
            )}
            {selectedRunId && reportLoading && (
              <div className="flex-1 flex items-center justify-center text-sm text-mist-400">
                Loading report…
              </div>
            )}
            {selectedRunId && !reportLoading && reportError && (
              <div className="flex-1 flex items-center justify-center p-8 text-center">
                <div>
                  <p className="text-sm text-rose-500">{reportError}</p>
                  <p className="text-xs text-mist-400 mt-2">
                    Removing scan from list because compliance report was not found.
                  </p>
                </div>
              </div>
            )}
            {selectedRunId && !reportLoading && !reportError && reportUrl && (
              <>
                <div className="flex items-center justify-between px-4 py-2.5 border-b border-ink-700 bg-ink-900/60">
                  <span className="text-xs font-mono text-mist-300 truncate max-w-[300px]">
                    {selectedRun?.file_name} &middot; {label} Compliance Report
                  </span>
                  <div className="flex items-center gap-2">
                    <a
                      href={reportUrl}
                      download={`${selectedRunId}-compliance-report.html`}
                      className="btn-secondary !py-1 !px-3 text-xs"
                    >
                      Download HTML
                    </a>
                    <button
                      onClick={printPdf}
                      className="btn-secondary !py-1 !px-3 text-xs"
                    >
                      Download PDF
                    </button>
                  </div>
                </div>
                <iframe
                  id="complianceReportIframe"
                  title={`${label} compliance report`}
                  src={reportUrl}
                  className="flex-1 w-full bg-white"
                />
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
