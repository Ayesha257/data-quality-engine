const toneFor = (score) => {
  if (score == null) return { stroke: "#3C4A5C", text: "text-mist-400" };
  if (score >= 85) return { stroke: "#3FD1C6", text: "text-teal-400" };
  if (score >= 65) return { stroke: "#E8A33D", text: "text-amber-400" };
  return { stroke: "#E86A6A", text: "text-rose-500" };
};

/** Analog-meter style score gauge. score is 0-100 or null (pending). */
export default function ScoreDial({ score, label = "Overall Score", size = 168 }) {
  const radius = 70;
  const circumference = 2 * Math.PI * radius;
  const pct = score == null ? 0 : Math.max(0, Math.min(100, score));
  const dash = (pct / 100) * circumference;
  const { stroke, text } = toneFor(score);
  const ticks = Array.from({ length: 24 });

  return (
    <div className="flex flex-col items-center" style={{ width: size }}>
      <svg viewBox="0 0 180 180" width={size} height={size}>
        {ticks.map((_, i) => {
          const angle = (i / ticks.length) * 360;
          const major = i % 6 === 0;
          return (
            <line
              key={i}
              x1="90"
              y1={major ? "10" : "14"}
              x2="90"
              y2="20"
              stroke="#2A3441"
              strokeWidth={major ? 2 : 1}
              transform={`rotate(${angle} 90 90)`}
            />
          );
        })}
        <circle cx="90" cy="90" r={radius} fill="none" stroke="#1E2632" strokeWidth="10" />
        <circle
          cx="90"
          cy="90"
          r={radius}
          fill="none"
          stroke={stroke}
          strokeWidth="10"
          strokeLinecap="round"
          strokeDasharray={`${dash} ${circumference}`}
          transform="rotate(-90 90 90)"
          style={{ transition: "stroke-dasharray 0.6s ease" }}
        />
        <text
          x="90"
          y="86"
          textAnchor="middle"
          className={`font-mono font-semibold ${text}`}
          fill="currentColor"
          fontSize="34"
        >
          {score == null ? "—" : score.toFixed(1)}
        </text>
        <text x="90" y="106" textAnchor="middle" fill="#5C6B7F" fontSize="11" fontFamily="IBM Plex Mono, monospace">
          / 100
        </text>
      </svg>
      <span className="text-xs font-mono uppercase tracking-wider text-mist-300 -mt-2">
        {label}
      </span>
    </div>
  );
}
