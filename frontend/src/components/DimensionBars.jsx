const barTone = (v) => (v >= 85 ? "bg-teal-500" : v >= 65 ? "bg-amber-500" : "bg-rose-500");

// `scores` is dimension_scores from RunResultsResponse: each entry is now
// a rich object -- { score, passed, total, skipped, errored, weight,
// available } -- as produced by engine/scoring.py, not a bare number.
// `available` is false (and `score` is null) when a dimension had no
// results and was excluded from the composite score (e.g. freshness with
// no date column supplied) -- those render as "not scored" instead of a
// bar, rather than crashing on `null.toFixed()`.
export default function DimensionBars({ scores }) {
  const entries = Object.entries(scores || {}).sort((a, b) => {
    const av = a[1]?.available ? a[1].score : -1;
    const bv = b[1]?.available ? b[1].score : -1;
    return bv - av;
  });
  if (entries.length === 0) {
    return <p className="text-sm text-mist-400">No dimension breakdown available.</p>;
  }
  return (
    <div className="space-y-3">
      {entries.map(([name, info]) => {
        const available = info?.available && typeof info?.score === "number";
        const value = available ? info.score : 0;
        return (
          <div key={name}>
            <div className="flex items-center justify-between mb-1">
              <span className="text-sm text-mist-200 capitalize">{name.replace(/_/g, " ")}</span>
              <span className="font-mono text-xs text-mist-300">
                {available ? value.toFixed(1) : "not scored"}
              </span>
            </div>
            <div className="h-2 rounded-full bg-ink-800 overflow-hidden">
              {available && (
                <div
                  className={`h-full rounded-full ${barTone(value)} transition-all duration-700`}
                  style={{ width: `${Math.max(2, Math.min(100, value))}%` }}
                />
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}
