import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../context/AuthContext.jsx";
import { api } from "../api/client.js";

function initialsFromProfile(profile, email) {
  const name = profile?.full_name?.trim();
  if (name) {
    const parts = name.split(/\s+/).filter(Boolean);
    if (parts.length >= 2) return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
    return parts[0].slice(0, 2).toUpperCase();
  }
  const local = (email || "").split("@")[0] || "?";
  return local.slice(0, 2).toUpperCase();
}

function formatDate(iso) {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString(undefined, {
    year: "numeric",
    month: "long",
    day: "numeric",
  });
}

function StatCard({ label, value, hint, accent = "teal" }) {
  const accentClass =
    accent === "amber"
      ? "text-amber-400"
      : accent === "rose"
        ? "text-rose-400"
        : "text-teal-400";
  return (
    <div className="panel p-5">
      <p className="field-label">{label}</p>
      <p className={`font-display text-3xl font-semibold mt-2 ${accentClass}`}>{value}</p>
      {hint && <p className="field-hint">{hint}</p>}
    </div>
  );
}

export default function ProfilePage() {
  const { email, clientId, updateDisplayName } = useAuth();
  const [profile, setProfile] = useState(null);
  const [stats, setStats] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const [nameDraft, setNameDraft] = useState("");
  const [savingName, setSavingName] = useState(false);
  const [nameSuccess, setNameSuccess] = useState(null);
  const [nameError, setNameError] = useState(null);

  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [savingPassword, setSavingPassword] = useState(false);
  const [passwordMessage, setPasswordMessage] = useState(null);
  const [passwordError, setPasswordError] = useState(null);

  const [copied, setCopied] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    Promise.all([api.getProfile(), api.getProfileStats()])
      .then(([profileData, statsData]) => {
        if (cancelled) return;
        setProfile(profileData);
        setStats(statsData);
        setNameDraft(profileData.full_name || "");
      })
      .catch((e) => {
        if (!cancelled) setError(e.message);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const initials = useMemo(
    () => initialsFromProfile(profile, profile?.email || email),
    [profile, email]
  );

  const saveName = async (e) => {
    e.preventDefault();
    setSavingName(true);
    setNameSuccess(null);
    setNameError(null);
    try {
      const updated = await api.updateProfile({ fullName: nameDraft.trim() || null });
      setProfile(updated);
      updateDisplayName(updated.full_name);
      setNameSuccess("Display name saved.");
    } catch (e2) {
      setNameError(e2.message);
    } finally {
      setSavingName(false);
    }
  };

  const savePassword = async (e) => {
    e.preventDefault();
    setPasswordError(null);
    setPasswordMessage(null);
    if (newPassword !== confirmPassword) {
      setPasswordError("New passwords do not match.");
      return;
    }
    if (newPassword.length < 8) {
      setPasswordError("Password must be at least 8 characters.");
      return;
    }
    setSavingPassword(true);
    try {
      await api.changePassword({ currentPassword, newPassword });
      setCurrentPassword("");
      setNewPassword("");
      setConfirmPassword("");
      setPasswordMessage("Password updated successfully.");
    } catch (e2) {
      setPasswordError(e2.message);
    } finally {
      setSavingPassword(false);
    }
  };

  const copyClientId = async () => {
    const value = profile?.client_id || clientId;
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      /* clipboard may be unavailable */
    }
  };

  if (loading) {
    return (
      <div className="max-w-4xl mx-auto space-y-6 animate-pulseSoft">
        <div className="h-8 w-48 bg-ink-800 rounded-lg" />
        <div className="panel h-40" />
        <div className="grid sm:grid-cols-3 gap-4">
          <div className="panel h-28" />
          <div className="panel h-28" />
          <div className="panel h-28" />
        </div>
      </div>
    );
  }

  return (
    <div className="max-w-4xl mx-auto space-y-8">
      <div>
        <h1 className="font-display text-2xl font-semibold text-mist-100">Profile</h1>
        <p className="text-sm text-mist-400 mt-1">
          Manage your account, review scan activity, and update security settings.
        </p>
      </div>

      {error && (
        <div className="text-sm text-rose-500 bg-rose-500/10 border border-rose-500/30 rounded-lg px-3 py-2">
          {error}
        </div>
      )}

      {/* Hero card */}
      <div className="panel overflow-hidden">
        <div className="h-1 bg-gradient-to-r from-teal-500 via-teal-400 to-teal-600" />
        <div className="p-6 sm:p-8 flex flex-col sm:flex-row sm:items-center gap-6">
          <div className="w-16 h-16 rounded-2xl bg-ink-800 border border-teal-500/30 flex items-center justify-center shrink-0">
            <span className="font-display text-xl font-semibold text-teal-400">{initials}</span>
          </div>
          <div className="flex-1 min-w-0">
            <h2 className="font-display text-xl font-semibold text-mist-100 truncate">
              {profile?.full_name || "Your account"}
            </h2>
            <p className="font-mono text-sm text-mist-300 mt-1 truncate">{profile?.email || email}</p>
            <div className="flex flex-wrap items-center gap-3 mt-3">
              <span className="badge bg-ink-800 text-mist-300 border border-ink-600">
                Member since {formatDate(profile?.created_at)}
              </span>
              <span className="badge bg-teal-500/10 text-teal-400 border border-teal-500/20">
                Active
              </span>
            </div>
          </div>
          <Link to="/runs" className="btn-secondary shrink-0">
            View runs
          </Link>
        </div>
      </div>

      {/* Stats */}
      <div>
        <h3 className="font-display text-lg font-medium text-mist-100 mb-4">Scan activity</h3>
        <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-4">
          <StatCard label="Total scans" value={stats?.total_runs ?? 0} hint="All pipeline runs" />
          <StatCard
            label="Completed"
            value={stats?.completed_runs ?? 0}
            hint="Successful runs"
            accent="teal"
          />
          <StatCard
            label="Failed"
            value={stats?.failed_runs ?? 0}
            hint="Runs that errored"
            accent="rose"
          />
          <StatCard
            label="Avg score"
            value={stats?.average_score != null ? `${stats.average_score}` : "—"}
            hint="Completed runs only"
            accent="amber"
          />
        </div>
      </div>

      <div className="grid lg:grid-cols-2 gap-6">
        {/* Account details */}
        <section className="panel p-6 space-y-5">
          <div>
            <h3 className="font-display text-lg font-medium text-mist-100">Account details</h3>
            <p className="text-sm text-mist-400 mt-1">Update how your name appears across DQE.</p>
          </div>

          <form onSubmit={saveName} className="space-y-4">
            <div>
              <label className="field-label" htmlFor="fullName">
                Display name
              </label>
              <input
                id="fullName"
                className="field-input"
                value={nameDraft}
                onChange={(e) => setNameDraft(e.target.value)}
                placeholder="Jane Doe"
                autoComplete="name"
              />
            </div>

            <div>
              <p className="field-label">Email</p>
              <p className="font-mono text-sm text-mist-200 bg-ink-800/60 border border-ink-700 rounded-lg px-3.5 py-2.5">
                {profile?.email || email}
              </p>
              <p className="field-hint">Email is fixed at signup and cannot be changed here.</p>
            </div>

            <div>
              <p className="field-label">Client ID</p>
              <div className="flex gap-2">
                <p className="flex-1 font-mono text-sm text-mist-300 bg-ink-800/60 border border-ink-700 rounded-lg px-3.5 py-2.5 truncate">
                  {profile?.client_id || clientId}
                </p>
                <button type="button" className="btn-secondary !px-3" onClick={copyClientId}>
                  {copied ? "Copied" : "Copy"}
                </button>
              </div>
              <p className="field-hint">
                Scopes all uploads, runs, and rules to your workspace automatically.
              </p>
            </div>

            {nameError && (
              <div className="text-sm text-rose-500 bg-rose-500/10 border border-rose-500/30 rounded-lg px-3 py-2">
                {nameError}
              </div>
            )}
            {nameSuccess && <p className="text-sm text-teal-400">{nameSuccess}</p>}

            <button type="submit" className="btn-primary" disabled={savingName}>
              {savingName ? "Saving…" : "Save changes"}
            </button>
          </form>
        </section>

        {/* Security */}
        <section className="panel p-6 space-y-5">
          <div>
            <h3 className="font-display text-lg font-medium text-mist-100">Security</h3>
            <p className="text-sm text-mist-400 mt-1">Change your password to keep your account secure.</p>
          </div>

          <form onSubmit={savePassword} className="space-y-4">
            <div>
              <label className="field-label" htmlFor="currentPassword">
                Current password
              </label>
              <input
                id="currentPassword"
                type="password"
                className="field-input"
                value={currentPassword}
                onChange={(e) => setCurrentPassword(e.target.value)}
                autoComplete="current-password"
                required
              />
            </div>
            <div>
              <label className="field-label" htmlFor="newPassword">
                New password
              </label>
              <input
                id="newPassword"
                type="password"
                className="field-input"
                value={newPassword}
                onChange={(e) => setNewPassword(e.target.value)}
                autoComplete="new-password"
                minLength={8}
                required
              />
            </div>
            <div>
              <label className="field-label" htmlFor="confirmPassword">
                Confirm new password
              </label>
              <input
                id="confirmPassword"
                type="password"
                className="field-input"
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                autoComplete="new-password"
                minLength={8}
                required
              />
            </div>

            {passwordError && (
              <div className="text-sm text-rose-500 bg-rose-500/10 border border-rose-500/30 rounded-lg px-3 py-2">
                {passwordError}
              </div>
            )}
            {passwordMessage && <p className="text-sm text-teal-400">{passwordMessage}</p>}

            <button type="submit" className="btn-primary" disabled={savingPassword}>
              {savingPassword ? "Updating…" : "Update password"}
            </button>
          </form>
        </section>
      </div>
    </div>
  );
}
