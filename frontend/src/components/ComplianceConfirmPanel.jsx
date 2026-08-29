import { useState } from "react";

/**
 * Renders the human-in-the-loop compliance column checkpoint (see
 * backend/engine/api_prompt.py). `confirmation` is
 * RunStatusResponse.pending_confirmation -- shaped:
 *   { prompt_type: "COMPLIANCE_COLUMN_CONFIRM", type: "compliance_column",
 *     sheet_name, findings: [{ column_name, guessed_field, regulation, confidence }] }
 */
export default function ComplianceConfirmPanel({ confirmation, onConfirm, submitting }) {
  const rawFindings = confirmation.findings || (
    confirmation.column_name
      ? [{
          column_name: confirmation.column_name,
          guessed_field: confirmation.guessed_field,
          regulation: confirmation.regulation,
          confidence: confirmation.confidence || "low",
        }]
      : []
  );

  // Map each column_name -> boolean (true = confirm, false = reject)
  // Default to true (confirm) for human review
  const [decisions, setDecisions] = useState(() => {
    const initial = {};
    rawFindings.forEach((f) => {
      if (f.column_name) initial[f.column_name] = true;
    });
    return initial;
  });

  const toggleDecision = (col, value) => {
    setDecisions((prev) => ({ ...prev, [col]: value }));
  };

  const setAll = (value) => {
    const updated = {};
    rawFindings.forEach((f) => {
      if (f.column_name) updated[f.column_name] = value;
    });
    setDecisions(updated);
  };

  const handleSubmit = () => {
    const decisionList = rawFindings.map((f) => ({
      column_name: f.column_name,
      guessed_field: f.guessed_field,
      regulation: f.regulation,
      confirmed: Boolean(decisions[f.column_name]),
    }));
    onConfirm({ decisions: decisionList });
  };

  return (
    <div className="panel p-6 space-y-5 border-teal-500/30">
      <div className="flex items-start gap-3">
        <span className="mt-0.5 h-2 w-2 rounded-full bg-teal-400 animate-pulseSoft shrink-0" />
        <div>
          <h2 className="font-display font-semibold text-mist-100">
            Confirm Compliance Findings{confirmation.sheet_name ? ` — ${confirmation.sheet_name}` : ""}
          </h2>
          <p className="text-sm text-mist-400 mt-1">
            Compliance candidate findings require human verification before being finalized for the report.
            Please review each finding below and decide whether to confirm or reject it for report inclusion.
          </p>
        </div>
      </div>

      {rawFindings.length > 0 && (
        <div className="rounded-lg border border-ink-700 overflow-x-auto">
          <table className="w-full text-xs font-mono">
            <thead>
              <tr className="bg-ink-800/80 text-mist-300 border-b border-ink-700 text-left">
                <th className="px-4 py-2 font-medium">Column Name</th>
                <th className="px-4 py-2 font-medium">Guessed Field</th>
                <th className="px-4 py-2 font-medium">Regulation</th>
                <th className="px-4 py-2 font-medium">Confidence</th>
                <th className="px-4 py-2 font-medium text-right">Decision</th>
              </tr>
            </thead>
            <tbody>
              {rawFindings.map((finding) => {
                const col = finding.column_name;
                const isConfirmed = Boolean(decisions[col]);
                const regLabel = String(finding.regulation || "Compliance").replace("_", "-");
                const confLabel = finding.confidence === "medium" ? "Medium" : "Low (Heuristic)";

                return (
                  <tr
                    key={col}
                    className={`border-b border-ink-800 last:border-0 transition-colors ${
                      isConfirmed ? "bg-teal-500/5 text-mist-100" : "bg-ink-900/40 text-mist-400 opacity-75"
                    }`}
                  >
                    <td className="px-4 py-2.5 font-semibold text-mist-100 whitespace-nowrap">
                      {col}
                    </td>
                    <td className="px-4 py-2.5 text-mist-300 whitespace-nowrap">
                      {finding.guessed_field || "Heuristic Match"}
                    </td>
                    <td className="px-4 py-2.5 whitespace-nowrap">
                      <span className="px-2 py-0.5 rounded bg-ink-800 border border-ink-700 text-teal-400 text-[11px]">
                        {regLabel}
                      </span>
                    </td>
                    <td className="px-4 py-2.5 whitespace-nowrap">
                      <span className="px-2 py-0.5 rounded bg-amber-500/10 border border-amber-500/30 text-amber-300 text-[11px]">
                        {confLabel}
                      </span>
                    </td>
                    <td className="px-4 py-2.5 whitespace-nowrap text-right">
                      <div className="inline-flex rounded-md shadow-sm" role="group">
                        <button
                          type="button"
                          disabled={submitting}
                          onClick={() => toggleDecision(col, true)}
                          className={`px-2.5 py-1 text-xs font-medium rounded-l-md border transition-colors ${
                            isConfirmed
                              ? "bg-teal-500 text-ink-950 font-semibold border-teal-500"
                              : "bg-ink-800 text-mist-300 border-ink-700 hover:bg-ink-700"
                          }`}
                        >
                          Confirm
                        </button>
                        <button
                          type="button"
                          disabled={submitting}
                          onClick={() => toggleDecision(col, false)}
                          className={`px-2.5 py-1 text-xs font-medium rounded-r-md border transition-colors ${
                            !isConfirmed
                              ? "bg-rose-500/20 text-rose-300 border-rose-500/40 font-semibold"
                              : "bg-ink-800 text-mist-300 border-ink-700 hover:bg-ink-700"
                          }`}
                        >
                          Reject
                        </button>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      <div className="flex flex-wrap items-center justify-between gap-3 pt-1">
        <div className="flex items-center gap-2">
          <button
            type="button"
            disabled={submitting}
            onClick={() => setAll(true)}
            className="text-xs text-teal-400 hover:text-teal-300 underline"
          >
            Confirm all
          </button>
          <span className="text-mist-600">&middot;</span>
          <button
            type="button"
            disabled={submitting}
            onClick={() => setAll(false)}
            className="text-xs text-mist-400 hover:text-mist-300 underline"
          >
            Reject all
          </button>
        </div>

        <button
          type="button"
          disabled={submitting}
          onClick={handleSubmit}
          className="btn-primary disabled:opacity-50"
        >
          {submitting ? "Submitting Decisions…" : "Submit Resolutions & Continue"}
        </button>
      </div>

      <p className="text-xs text-mist-500">
        Confirmed findings will be included in the verified section of the compliance report.
        Rejected findings will be discarded and excluded from the report.
      </p>
    </div>
  );
}
