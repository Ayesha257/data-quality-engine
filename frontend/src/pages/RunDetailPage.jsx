import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api/client.js";
import StatusBadge from "../components/StatusBadge.jsx";
import ScoreDial from "../components/ScoreDial.jsx";
import DimensionBars from "../components/DimensionBars.jsx";
import EntityResolutionPanel from "../components/EntityResolutionPanel.jsx";
import HeaderConfirmPanel from "../components/HeaderConfirmPanel.jsx";
import ComplianceConfirmPanel from "../components/ComplianceConfirmPanel.jsx";

const TERMINAL = new Set(["completed", "failed"]);

export default function RunDetailPage() {
  const { runId } = useParams();
  const [status, setStatus] = useState(null);
  const [results, setResults] = useState(null);
  const [entityResolution, setEntityResolution] = useState(null);
  const [error, setError] = useState(null);
  const [reportUrl, setReportUrl] = useState(null);
  const [reportSheet, setReportSheet] = useState(null);
  const [reportLoading, setReportLoading] = useState(false);
  const [pdfLoading, setPdfLoading] = useState(false);
  const [pdfError, setPdfError] = useState(null);
  const [confirmSubmitting, setConfirmSubmitting] = useState(false);
  const pollRef = useRef(null);

  useEffect(() => {
    let cancelled = false;

    const tick = async () => {
      try {
        const s = await api.getRunStatus(runId);
        if (cancelled) return;
        setStatus(s);
        if (TERMINAL.has(s.status)) {
          const r = await api.getRunResults(runId);
          if (!cancelled) setResults(r);
          if (s.status === "completed") {
            try {
              const er = await api.getEntityResolutionResults(runId);
              if (!cancelled) setEntityResolution(er);
            } catch {
              if (!cancelled) setEntityResolution(null);
            }
          }
          clearInterval(pollRef.current);
        }
      } catch (e) {
        if (!cancelled) {
          setError(e.message);
          clearInterval(pollRef.current);
        }
      }
    };

    tick();
    pollRef.current = setInterval(tick, 2500);
    return () => {
      cancelled = true;
      clearInterval(pollRef.current);
    };
  }, [runId]);

  useEffect(() => () => reportUrl && URL.revokeObjectURL(reportUrl), [reportUrl]);

  const submitConfirmation = async ({ accept, overrideHeaderRow }) => {
    setConfirmSubmitting(true);
    try {
      await api.confirmRun(runId, { accept, overrideHeaderRow });
      // Refresh immediately instead of waiting for the next 2.5s poll tick,
      // so the panel doesn't just sit there after a successful submit.
      const s = await api.getRunStatus(runId);
      setStatus(s);
    } catch (e) {
      setError(e.message);
    } finally {
      setConfirmSubmitting(false);
    }
  };

  const submitComplianceConfirmation = async ({ decisions }) => {
    setConfirmSubmitting(true);
    try {
      await api.confirmCompliance(runId, decisions);
      const s = await api.getRunStatus(runId);
      setStatus(s);
    } catch (e) {
      setError(e.message);
    } finally {
      setConfirmSubmitting(false);
    }
  };

  const openReport = async (sheetName) => {
    setReportLoading(true);
    setReportSheet(sheetName || null);
    try {
      if (reportUrl) URL.revokeObjectURL(reportUrl);
      const url = await api.fetchReportBlobUrl(runId, sheetName);
      setReportUrl(url);
    } catch (e) {
      setError(e.message);
    } finally {
      setReportLoading(false);
    }
  };

  // Fetches the real PDF as an authenticated blob, then triggers a normal
  // file download via a throwaway <a download> click -- no print dialog,
  // no "Save as PDF" step, just a file landing in Downloads.
  const downloadPdf = async () => {
    setPdfLoading(true);
    setPdfError(null);
    let url;
    try {
      url = await api.fetchReportPdfBlobUrl(runId, reportSheet);
      const link = document.createElement("a");
      link.href = url;
      link.download = `${runId}${reportSheet ? `-${reportSheet}` : ""}-report.pdf`;
      document.body.appendChild(link);
      link.click();
      link.remove();
    } catch (e) {
      setPdfError(e.message);
    } finally {
      if (url) URL.revokeObjectURL(url);
      setPdfLoading(false);
    }
  };

  if (error) {
    return (
      <div className="panel p-6 text-sm text-rose-500 border-rose-500/30">
        <p className="font-medium mb-1">Couldn't load this run</p>
        <p>{error}</p>
        <Link to="/runs" className="btn-secondary mt-4 inline-flex">
          Back to runs
        </Link>
      </div>
    );
  }

  if (!status) return <LoadingState />;

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between flex-wrap gap-3">
        <div>
          <Link to="/runs" className="text-xs text-mist-400 hover:text-mist-200 font-mono">
            ← Runs
          </Link>
          <h1 className="font-display text-2xl font-semibold text-mist-100 mt-1">
            {status.file_name}
          </h1>
          <p className="text-xs text-mist-400 font-mono mt-1">{runId}</p>
        </div>
        <StatusBadge status={status.status} />
      </div>

      {(status.status === "pending" || status.status === "running") && (
        <div className="panel p-8 flex flex-col items-center text-center">
          <ScanningGlyph />
          <p className="text-mist-200 font-medium mt-4">
            {status.status === "pending" ? "Queued for processing…" : "Scanning dataset…"}
          </p>
          <p className="text-sm text-mist-400 mt-1">
            This page updates automatically — checks run per sheet, then scores are composed.
          </p>
        </div>
      )}

      {status.status === "awaiting_confirmation" && status.pending_confirmation && (
        status.pending_confirmation.prompt_type === "COMPLIANCE_COLUMN_CONFIRM" ||
        status.pending_confirmation.type === "compliance_column" ||
        Boolean(status.pending_confirmation.findings) ? (
          <ComplianceConfirmPanel
            confirmation={status.pending_confirmation}
            onConfirm={submitComplianceConfirmation}
            submitting={confirmSubmitting}
          />
        ) : (
          <HeaderConfirmPanel
            confirmation={status.pending_confirmation}
            onConfirm={submitConfirmation}
            submitting={confirmSubmitting}
          />
        )
      )}

      {status.status === "failed" && (
        <div className="panel p-6 border-rose-500/30">
          <p className="font-medium text-rose-500 mb-1">Run failed</p>
          <p className="text-sm text-mist-300">{status.error_message || results?.error_message || "No error detail was returned."}</p>
        </div>
      )}

      {status.status === "completed" && results && (
        <>
          <div className="panel p-6 grid grid-cols-1 md:grid-cols-[auto_1fr] gap-8 items-center">
            <ScoreDial score={results.overall_score} />
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
              <Stat label="Rows" value={results.rows_processed ?? "—"} />
              <Stat label="Columns" value={results.cols_processed ?? "—"} />
              <Stat label="Sheets" value={results.sheets.length} />
              <Stat label="Client" value={results.client_id} mono />
            </div>
          </div>

          {results.dimension_scores && (
            <div className="panel p-6">
              <h2 className="font-display font-semibold text-mist-100 mb-4">Dimension breakdown</h2>
              <DimensionBars scores={results.dimension_scores} />
            </div>
          )}

          <EntityResolutionPanel
            sheets={
              entityResolution?.sheets?.length
                ? entityResolution.sheets
                : results.sheets.filter(
                    (s) =>
                      s.entity_resolution_auto != null ||
                      s.entity_resolution_review != null ||
                      s.entity_resolution_no_match != null ||
                      s.entity_resolution?.enabled
                  ).map((s) => ({
                    sheet_name: s.sheet_name,
                    enabled: s.entity_resolution?.enabled,
                    summary: s.entity_resolution?.summary,
                    columns: s.entity_resolution?.columns,
                    review_queue: s.entity_resolution?.review_queue,
                    entity_resolution_auto: s.entity_resolution_auto,
                    entity_resolution_review: s.entity_resolution_review,
                    entity_resolution_no_match: s.entity_resolution_no_match,
                  }))
            }
          />

          <div className="panel overflow-hidden">
            <div className="px-6 py-4 border-b border-ink-700">
              <h2 className="font-display font-semibold text-mist-100">Sheets</h2>
            </div>
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs font-mono uppercase tracking-wider text-mist-400 border-b border-ink-700">
                  <th className="px-6 py-3 font-medium">Sheet</th>
                  <th className="px-6 py-3 font-medium">Rows × Cols</th>
                  <th className="px-6 py-3 font-medium">Quality</th>
                  <th className="px-6 py-3 font-medium">Privacy risk</th>
                  <th className="px-6 py-3 font-medium">Forecast readiness</th>
                  <th className="px-6 py-3 font-medium" />
                </tr>
              </thead>
              <tbody>
                {results.sheets.map((sheet) => (
                  <tr key={sheet.sheet_name} className="border-b border-ink-800 last:border-0">
                    <td className="px-6 py-3.5 font-mono text-mist-100">{sheet.sheet_name}</td>
                    <td className="px-6 py-3.5 text-mist-300">
                      {sheet.rows ?? "—"} × {sheet.columns ?? "—"}
                    </td>
                    <td className="px-6 py-3.5 text-mist-300">
                      {sheet.data_quality_score != null ? sheet.data_quality_score.toFixed(1) : "—"}
                    </td>
                    <td className="px-6 py-3.5 text-mist-300">{sheet.privacy_risk_level ?? "—"}</td>
                    <td className="px-6 py-3.5 text-mist-300">
                      {formatReadinessVerdict(sheet.ml_readiness_verdict)}
                      {sheet.ml_readiness_score != null && (
                        <span className="text-mist-400"> ({sheet.ml_readiness_score.toFixed(0)})</span>
                      )}
                    </td>
                    <td className="px-6 py-3.5 text-right">
                      {sheet.report_path ? (
                        <button
                          onClick={() => openReport(sheet.sheet_name)}
                          className="text-teal-400 hover:text-teal-300 font-medium text-sm"
                        >
                          Quality report →
                        </button>
                      ) : sheet.error ? (
                        <span className="text-rose-500 text-xs">{sheet.error}</span>
                      ) : (
                        <span className="text-mist-400 text-xs">no report</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {(reportUrl || reportLoading) && (
            <div className="panel overflow-hidden">
              <div className="px-6 py-4 border-b border-ink-700 flex items-center justify-between">
                <h2 className="font-display font-semibold text-mist-100">
                  Quality report{reportSheet ? ` — ${reportSheet}` : ""}
                </h2>
                {reportUrl && (
                  <div className="flex items-center gap-2">
                    <a href={reportUrl} download={`${runId}-report.html`} className="btn-secondary !py-1.5 !px-3 text-xs">
                      Download HTML
                    </a>
                    <button
                      onClick={downloadPdf}
                      disabled={pdfLoading}
                      className="btn-secondary !py-1.5 !px-3 text-xs disabled:opacity-50"
                    >
                      {pdfLoading ? "Preparing PDF…" : "Download PDF"}
                    </button>
                  </div>
                )}
              </div>
              {pdfError && (
                <div className="px-6 py-2 text-xs text-rose-500 border-b border-ink-700">
                  Couldn't download the PDF: {pdfError}
                </div>
              )}
              {reportLoading ? (
                <div className="p-10 text-center text-mist-400 text-sm">Loading report…</div>
              ) : (
                <iframe
                  title="Quality report"
                  src={reportUrl}
                  className="w-full h-[720px] bg-white"
                />
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}

function formatReadinessVerdict(verdict) {
  if (!verdict) return "—";
  return verdict
    .replace(/_/g, " ")
    .toLowerCase()
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

function Stat({ label, value, mono }) {
  return (
    <div>
      <p className="text-xs font-mono uppercase tracking-wider text-mist-400 mb-1">{label}</p>
      <p className={`text-lg text-mist-100 font-semibold ${mono ? "font-mono !text-sm" : ""}`}>{value}</p>
    </div>
  );
}

function LoadingState() {
  return (
    <div className="panel p-8 flex flex-col items-center text-center">
      <div className="h-10 w-10 rounded-full border-2 border-ink-600 border-t-teal-500 animate-spin" />
      <p className="text-mist-400 text-sm mt-4">Loading run…</p>
    </div>
  );
}

function ScanningGlyph() {
  return (
    <div className="relative w-16 h-16 rounded-xl border border-ink-600 bg-ink-800 overflow-hidden">
      <div className="absolute inset-x-0 h-6 bg-gradient-to-b from-transparent via-teal-500/40 to-transparent animate-scan" />
      <svg viewBox="0 0 24 24" className="absolute inset-0 m-auto w-8 h-8 text-teal-400" fill="none">
        <path d="M4 6h16M4 12h16M4 18h10" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
      </svg>
    </div>
  );
}
