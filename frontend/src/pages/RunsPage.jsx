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
      .then((all) => {
        setRuns(all || []);
      })
      .catch((e) => setError(e.message));
  };

  const handleDelete = async (runId, fileName) => {
    if (
      !window.confirm(
        `Are you sure you want to delete the scan for "${fileName}"? This will permanently remove its reports and data.`
      )
    ) {
      return;
    }
    try {
      await api.deleteRun(runId);
      setRuns((prev) => (prev ? prev.filter((r) => r.run_id !== runId) : []));
    } catch (e) {
      setError(`Failed to delete run: ${e.message}`);
    }
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
              Scan history could not be loaded. Try again in a moment, or start a new scan.
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
                <th className="px-5 py-3 font-medium text-right">Actions</th>
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
                  <td className="px-5 py-3.5 text-right whitespace-nowrap">
                    <Link to={`/runs/${run.run_id}`} className="text-teal-400 hover:text-teal-300 font-medium">
                      View →
                    </Link>
                    <button
                      type="button"
                      onClick={() => handleDelete(run.run_id, run.file_name)}
                      className="ml-3 text-rose-400 hover:text-rose-300 font-medium text-xs hover:underline cursor-pointer"
                      title="Delete run"
                    >
                      Delete
                    </button>
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
