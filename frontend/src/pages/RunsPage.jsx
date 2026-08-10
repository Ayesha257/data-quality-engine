import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../context/AuthContext.jsx";
import { api } from "../api/client.js";
import StatusBadge from "../components/StatusBadge.jsx";

export default function RunsPage() {
  const { clientId } = useAuth();
  const [runs, setRuns] = useState(null);
  const [error, setError] = useState(null);

  const load = () => {
    api
      .listRuns(clientId)
      .then(setRuns)
      .catch((e) => setError(e.message));
  };

  useEffect(() => {
    load();
    const id = setInterval(load, 8000);
    return () => clearInterval(id);
  }, [clientId]);

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="font-display text-2xl font-semibold text-mist-100">Runs</h1>
          <p className="text-sm text-mist-400 mt-1">Scan history for {clientId}</p>
        </div>
        <Link to="/upload" className="btn-primary">
          + New scan
        </Link>
      </div>

      {error && (
        <div className="panel p-4 mb-4 text-sm text-rose-500 border-rose-500/30">
          {error}
          {error.toLowerCase().includes("not found") && (
            <p className="text-mist-400 mt-1">
              This backend build may not yet have the list-runs endpoint — see{" "}
              <code className="text-mist-300">backend_patches/</code> in this delivery.
            </p>
          )}
        </div>
      )}

      {runs === null && !error && <SkeletonTable />}

      {runs && runs.length === 0 && (
        <div className="panel p-12 text-center">
          <p className="text-mist-300 font-medium">No scans yet</p>
          <p className="text-sm text-mist-400 mt-1">Upload a file to run your first quality scan.</p>
          <Link to="/upload" className="btn-primary mt-4 inline-flex">
            Start a scan
          </Link>
        </div>
      )}

      {runs && runs.length > 0 && (
        <div className="panel overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs font-mono uppercase tracking-wider text-mist-400 border-b border-ink-700">
                <th className="px-5 py-3 font-medium">File</th>
                <th className="px-5 py-3 font-medium">Status</th>
                <th className="px-5 py-3 font-medium">Score</th>
                <th className="px-5 py-3 font-medium">Started</th>
                <th className="px-5 py-3 font-medium" />
              </tr>
            </thead>
            <tbody>
              {runs.map((run) => (
                <tr key={run.run_id} className="border-b border-ink-800 last:border-0 hover:bg-ink-800/40">
                  <td className="px-5 py-3.5 font-mono text-mist-100">{run.file_name}</td>
                  <td className="px-5 py-3.5">
                    <StatusBadge status={run.status} />
                  </td>
                  <td className="px-5 py-3.5 font-mono text-mist-300">
                    {run.overall_score != null ? run.overall_score.toFixed(1) : "—"}
                  </td>
                  <td className="px-5 py-3.5 text-mist-400">
                    {new Date(run.started_at).toLocaleString()}
                  </td>
                  <td className="px-5 py-3.5 text-right">
                    <Link to={`/runs/${run.run_id}`} className="text-teal-400 hover:text-teal-300 font-medium">
                      View →
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function SkeletonTable() {
  return (
    <div className="panel p-5 space-y-3">
      {[...Array(4)].map((_, i) => (
        <div key={i} className="h-10 rounded-lg bg-ink-800 animate-pulseSoft" />
      ))}
    </div>
  );
}
