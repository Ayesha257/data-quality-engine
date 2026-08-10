import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext.jsx";
import { LogoMark } from "../components/Layout.jsx";

export default function RegisterPage() {
  const { register, status, error } = useAuth();
  const navigate = useNavigate();
  const [fullName, setFullName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [localError, setLocalError] = useState(null);

  const submit = async (e) => {
    e.preventDefault();
    setLocalError(null);

    if (password.length < 8) {
      setLocalError("Password must be at least 8 characters.");
      return;
    }
    if (password !== confirmPassword) {
      setLocalError("Passwords don't match.");
      return;
    }

    const ok = await register({ email: email.trim(), password, fullName: fullName.trim() });
    if (ok) navigate("/upload", { replace: true });
  };

  return (
    <div className="min-h-screen flex items-center justify-center relative overflow-hidden bg-ink-950">
      <div className="absolute inset-0 bg-scan-grid bg-[length:36px_36px] [mask-image:radial-gradient(ellipse_at_center,black,transparent_75%)]" />
      <div className="absolute top-0 left-0 right-0 h-px bg-gradient-to-r from-transparent via-teal-500/60 to-transparent animate-scan" />

      <div className="relative w-full max-w-sm mx-4">
        <div className="flex flex-col items-center mb-8">
          <div className="mb-3">
            <LogoMark size={40} />
          </div>
          <h1 className="font-display text-xl font-semibold text-mist-100">DQE</h1>
          <p className="text-sm text-mist-400 mt-1">Create your account to get started</p>
        </div>

        <form onSubmit={submit} className="panel p-6 space-y-4">
          <div>
            <label className="field-label" htmlFor="fullName">
              Name <span className="text-mist-500">(optional)</span>
            </label>
            <input
              id="fullName"
              className="field-input"
              placeholder="Jane Doe"
              value={fullName}
              onChange={(e) => setFullName(e.target.value)}
              autoComplete="name"
            />
          </div>
          <div>
            <label className="field-label" htmlFor="email">
              Email
            </label>
            <input
              id="email"
              type="email"
              className="field-input"
              placeholder="you@company.com"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              autoComplete="username"
              required
            />
          </div>
          <div>
            <label className="field-label" htmlFor="password">
              Password
            </label>
            <div className="relative">
              <input
                id="password"
                type={showPassword ? "text" : "password"}
                className="field-input pr-16"
                placeholder="At least 8 characters"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="new-password"
                required
                minLength={8}
              />
              <button
                type="button"
                onClick={() => setShowPassword((s) => !s)}
                className="absolute right-2 top-1/2 -translate-y-1/2 text-xs text-mist-400 hover:text-mist-200 px-2 py-1"
              >
                {showPassword ? "Hide" : "Show"}
              </button>
            </div>
          </div>
          <div>
            <label className="field-label" htmlFor="confirmPassword">
              Confirm password
            </label>
            <input
              id="confirmPassword"
              type={showPassword ? "text" : "password"}
              className="field-input"
              placeholder="Re-enter your password"
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              autoComplete="new-password"
              required
              minLength={8}
            />
          </div>

          {(localError || error) && (
            <div className="text-sm text-rose-500 bg-rose-500/10 border border-rose-500/30 rounded-lg px-3 py-2">
              {localError || error}
            </div>
          )}

          <button type="submit" className="btn-primary w-full" disabled={status === "checking"}>
            {status === "checking" ? "Creating account…" : "Create account"}
          </button>
        </form>

        <p className="text-center text-sm text-mist-400 mt-6">
          Already have an account?{" "}
          <Link to="/login" className="text-teal-400 hover:text-teal-300 font-medium">
            Sign in
          </Link>
        </p>
      </div>
    </div>
  );
}
