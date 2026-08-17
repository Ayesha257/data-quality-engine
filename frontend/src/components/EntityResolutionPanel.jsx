import { useState } from "react";

/** Value-matching summary and per-value detail table. */

export function flattenResolutions(sheet) {
  const rows = [];
  const columns = sheet?.columns || {};
  for (const [column, block] of Object.entries(columns)) {
    const resolutions = block.resolutions || {};
    for (const [original, rv] of Object.entries(resolutions)) {
      rows.push({
        id: `${column}:${original}`,
        column,
        entityType: block.entity_type,
        original,
        candidate: rv.candidate || "—",
        confidence: rv.confidence,
        decision: rv.decision,
        tier: rv.tier,
      });
    }
  }
  return rows;
}

function formatMatchMethod(tier) {
  const labels = {
    lookup: "Exact match",
    fuzzy: "Similar spelling",
    semantic: "Similar meaning",
  };
  return labels[tier] || tier || "—";
}

function DecisionBadge({ decision }) {
  const styles = {
    auto_match: "text-teal-400 bg-teal-500/10 border-teal-500/30",
    review: "text-amber-400 bg-amber-500/10 border-amber-500/30",
    no_match: "text-mist-300 bg-ink-800 border-ink-600",
  };
  const labels = {
    auto_match: "Auto",
    review: "Review",
    no_match: "No match",
  };
  const cls = styles[decision] || styles.no_match;
  return (
    <span className={`inline-flex px-2 py-0.5 rounded border text-xs font-mono ${cls}`}>
      {labels[decision] || decision}
    </span>
  );
}

export default function EntityResolutionPanel({ sheets = [], defaultOpen = false }) {
  const [openSheets, setOpenSheets] = useState(() => {
    if (!defaultOpen) return {};
    const initial = {};
    for (const s of sheets) {
      if (s?.sheet_name) initial[s.sheet_name] = true;
    }
    return initial;
  });

  const toggleSheet = (sheetName) => {
    setOpenSheets((prev) => ({
      ...prev,
      [sheetName]: !prev[sheetName],
    }));
  };

  const visible = sheets.filter(
    (s) =>
      s.enabled ||
      s.entity_resolution_auto != null ||
      s.entity_resolution_review != null ||
      s.entity_resolution_no_match != null
  );
  if (!visible.length) return null;

  return (
    <div className="panel p-6 space-y-6">
      <div>
        <h2 className="font-display font-semibold text-mist-100 mb-2">Standardized values</h2>
        <p className="text-sm text-mist-400">
          Suggested canonical spellings for places, codes, and similar fields. Your original
          data is never changed automatically.
        </p>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        {visible.map((sheet) => (
          <div
            key={sheet.sheet_name}
            className="rounded-lg border border-ink-700 bg-ink-900/40 p-4"
          >
            <p className="font-mono text-sm text-mist-200 mb-2">{sheet.sheet_name}</p>
            <div className="grid grid-cols-3 gap-2 text-center text-xs">
              <div>
                <p className="text-mist-400">Auto</p>
                <p className="text-teal-400 font-semibold text-lg">
                  {sheet.entity_resolution_auto ?? sheet.summary?.auto_match ?? 0}
                </p>
              </div>
              <div>
                <p className="text-mist-400">Review</p>
                <p className="text-amber-400 font-semibold text-lg">
                  {sheet.entity_resolution_review ?? sheet.summary?.review ?? 0}
                </p>
              </div>
              <div>
                <p className="text-mist-400">No match</p>
                <p className="text-mist-300 font-semibold text-lg">
                  {sheet.entity_resolution_no_match ?? sheet.summary?.no_match ?? 0}
                </p>
              </div>
            </div>
          </div>
        ))}
      </div>

      {visible.map((sheet) => {
        const rows = flattenResolutions(sheet);
        if (!rows.length) return null;
        const isOpen = !!openSheets[sheet.sheet_name];

        return (
          <div
            key={`${sheet.sheet_name}-detail`}
            className="overflow-hidden rounded-lg border border-ink-700 bg-ink-900/30"
          >
            <button
              type="button"
              onClick={() => toggleSheet(sheet.sheet_name)}
              className="w-full px-4 py-3 bg-ink-900/60 hover:bg-ink-800/60 transition-colors flex items-center justify-between text-left cursor-pointer select-none"
              aria-expanded={isOpen}
            >
              <div className="flex items-center gap-2.5">
                <span
                  className={`text-mist-400 transition-transform duration-200 text-sm inline-block font-mono ${
                    isOpen ? "rotate-90" : ""
                  }`}
                >
                  ›
                </span>
                <h3 className="text-sm font-medium text-mist-200">
                  Matched values — {sheet.sheet_name}
                </h3>
                <span className="text-xs font-mono text-mist-400 bg-ink-800 border border-ink-700 px-2 py-0.5 rounded">
                  {rows.length} {rows.length === 1 ? "value" : "values"}
                </span>
              </div>
              <span className="text-xs text-teal-400 font-medium hover:text-teal-300">
                {isOpen ? "Hide details" : "View details"}
              </span>
            </button>

            {isOpen && (
              <div className="overflow-x-auto border-t border-ink-700">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-left text-xs font-mono uppercase tracking-wider text-mist-400 border-b border-ink-700 bg-ink-900/40">
                      <th className="px-4 py-2 font-medium">Column</th>
                      <th className="px-4 py-2 font-medium">Original</th>
                      <th className="px-4 py-2 font-medium">Suggested value</th>
                      <th className="px-4 py-2 font-medium">Confidence</th>
                      <th className="px-4 py-2 font-medium">Method</th>
                      <th className="px-4 py-2 font-medium">Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((row) => (
                      <tr
                        key={row.id}
                        className="border-b border-ink-800 last:border-0 hover:bg-ink-800/30 transition-colors"
                      >
                        <td className="px-4 py-2 font-mono text-mist-300">{row.column}</td>
                        <td className="px-4 py-2 text-mist-200">{row.original}</td>
                        <td className="px-4 py-2 text-mist-200">{row.candidate}</td>
                        <td className="px-4 py-2 font-mono text-mist-300">
                          {row.confidence != null
                            ? `${(row.confidence * 100).toFixed(0)}%`
                            : "—"}
                        </td>
                        <td className="px-4 py-2 font-mono text-mist-400 text-xs">
                          {formatMatchMethod(row.tier)}
                        </td>
                        <td className="px-4 py-2">
                          <DecisionBadge decision={row.decision} />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
