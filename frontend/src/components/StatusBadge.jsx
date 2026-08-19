const CONFIG = {
  pending: { dot: "bg-mist-300", cls: "bg-ink-800 text-mist-300 border border-ink-600" },
  running: { dot: "bg-amber-500 animate-pulseSoft", cls: "bg-amber-500/10 text-amber-400 border border-amber-500/30" },
  awaiting_confirmation: {
    dot: "bg-violet-400 animate-pulseSoft",
    cls: "bg-violet-500/10 text-violet-300 border border-violet-500/30",
  },
  completed: { dot: "bg-teal-500", cls: "bg-teal-500/10 text-teal-400 border border-teal-500/30" },
  failed: { dot: "bg-rose-500", cls: "bg-rose-500/10 text-rose-500 border border-rose-500/30" },
};

const LABELS = {
  awaiting_confirmation: "needs input",
};

export default function StatusBadge({ status }) {
  const cfg = CONFIG[status] || CONFIG.pending;
  return (
    <span className={`badge ${cfg.cls}`}>
      <span className={`w-1.5 h-1.5 rounded-full ${cfg.dot}`} />
      {LABELS[status] || status}
    </span>
  );
}
