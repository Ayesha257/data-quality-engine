import { useState } from "react";

/**
 * Renders the human-in-the-loop header-row checkpoint (see
 * backend/engine/api_prompt.py). `confirmation` is
 * RunStatusResponse.pending_confirmation -- shaped:
 *   { type, sheet_name, message, detected_header_row, headerless,
 *     header_values, rows_above, rows_below, note }
 * rows_above/rows_below are each [{ row_index, values }].
 */
export default function HeaderConfirmPanel({ confirmation, onConfirm, submitting }) {
  const [overrideOpen, setOverrideOpen] = useState(false);
  const [overrideValue, setOverrideValue] = useState(
    confirmation.detected_header_row >= 0 ? String(confirmation.detected_header_row) : "0"
  );

  const allRows = [
    ...(confirmation.rows_above || []).map((r) => ({ ...r, kind: "context" })),
    ...(confirmation.headerless
      ? []
      : [{ row_index: confirmation.detected_header_row, values: confirmation.header_values, kind: "header" }]),
    ...(confirmation.rows_below || []).map((r) => ({ ...r, kind: "context" })),
  ].sort((a, b) => a.row_index - b.row_index);

  const submitOverride = () => {
    const parsed = parseInt(overrideValue, 10);
    if (Number.isNaN(parsed)) return;
    onConfirm({ accept: false, overrideHeaderRow: parsed });
  };

  return (
    <div className="panel p-6 space-y-5 border-violet-500/30">
      <div className="flex items-start gap-3">
        <span className="mt-0.5 h-2 w-2 rounded-full bg-violet-400 animate-pulseSoft shrink-0" />
        <div>
          <h2 className="font-display font-semibold text-mist-100">
            Confirm header row{confirmation.sheet_name ? ` — ${confirmation.sheet_name}` : ""}
          </h2>
          <p className="text-sm text-mist-400 mt-1">
            {confirmation.headerless
              ? confirmation.note ||
                "No credible header row was found on this sheet. It will load with synthetic column names (unnamed_0, unnamed_1, …)."
              : `Row ${confirmation.detected_header_row} looks like the header. Review the preview below before continuing.`}
          </p>
        </div>
      </div>

      {allRows.length > 0 && (
        <div className="rounded-lg border border-ink-700 overflow-x-auto">
          <table className="w-full text-xs font-mono">
            <tbody>
              {allRows.map((row) => (
                <tr
                  key={row.row_index}
                  className={
                    row.kind === "header"
                      ? "bg-violet-500/10 text-violet-200"
                      : "text-mist-400 odd:bg-ink-800/40"
                  }
                >
                  <td className="px-3 py-1.5 text-mist-500 whitespace-nowrap border-r border-ink-700">
                    {row.kind === "header" ? "→ " : ""}
                    row {row.row_index}
                  </td>
                  {(row.values || []).slice(0, 8).map((v, i) => (
                    <td key={i} className="px-3 py-1.5 whitespace-nowrap max-w-[160px] truncate">
                      {v === null || v === undefined || v === "" ? (
                        <span className="text-mist-500 italic">empty</span>
                      ) : (
                        String(v)
                      )}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="flex flex-wrap items-center gap-3 pt-1">
        <button
          type="button"
          disabled={submitting}
          onClick={() => onConfirm({ accept: true })}
          className="btn-primary disabled:opacity-50"
        >
          {submitting ? "Submitting…" : confirmation.headerless ? "Yes, load as headerless" : "Yes, that's correct"}
        </button>

        {!overrideOpen ? (
          <button
            type="button"
            disabled={submitting}
            onClick={() => setOverrideOpen(true)}
            className="btn-secondary disabled:opacity-50"
          >
            That's not right — pick a different row
          </button>
        ) : (
          <div className="flex items-center gap-2">
            <label className="text-xs text-mist-400">Header row index</label>
            <input
              type="number"
              className="field-input !w-24 !py-1.5"
              value={overrideValue}
              onChange={(e) => setOverrideValue(e.target.value)}
            />
            <button
              type="button"
              disabled={submitting}
              onClick={submitOverride}
              className="btn-primary !py-1.5 !px-3 text-sm disabled:opacity-50"
            >
              Use this row
            </button>
          </div>
        )}
      </div>
      <p className="text-xs text-mist-500">
        0-based row index within the raw sheet, counting from the very first row. Use -1 to load this sheet as
        headerless.
      </p>
    </div>
  );
}
