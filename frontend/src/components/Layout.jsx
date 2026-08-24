import { Link, NavLink, Outlet, useNavigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext.jsx";

const navItems = [
  { to: "/upload", label: "New Scan" },
  { to: "/runs", label: "Runs" },
  { to: "/rules", label: "Rules" },
  { to: "/compliance", label: "Compliance" },
  { to: "/profile", label: "Profile" },
];

export default function Layout() {
  const { email, fullName, logout } = useAuth();
  const navigate = useNavigate();

  return (
    <div className="min-h-screen flex flex-col">
      <header className="border-b border-ink-700 bg-ink-900/80 backdrop-blur sticky top-0 z-20">
        <div className="max-w-6xl mx-auto px-6 h-16 flex items-center justify-between">
          <div className="flex items-center gap-8">
            <div className="flex items-center gap-2.5">
              <LogoMark />
              <span className="font-display font-semibold text-mist-100 tracking-tight">
                DQE
              </span>
            </div>
            <nav className="flex items-center gap-1">
              {navItems.map((item) => (
                <NavLink
                  key={item.to}
                  to={item.to}
                  className={({ isActive }) =>
                    `px-3 py-2 rounded-lg text-sm font-medium transition-colors ${
                      isActive
                        ? "bg-ink-800 text-teal-400"
                        : "text-mist-300 hover:text-mist-100 hover:bg-ink-800/60"
                    }`
                  }
                >
                  {item.label}
                </NavLink>
              ))}
            </nav>
          </div>
          <div className="flex items-center gap-3">
            <Link
              to="/profile"
              className="hidden sm:flex items-center gap-2 px-3 py-1.5 rounded-full bg-ink-800 border border-ink-600 hover:border-teal-500/40 transition-colors"
            >
              <span className="w-1.5 h-1.5 rounded-full bg-teal-500" />
              <span className="font-mono text-xs text-mist-300 truncate max-w-[180px]">
                {fullName || email}
              </span>
            </Link>
            <button
              className="btn-ghost !px-3 !py-2 text-sm"
              onClick={() => {
                logout();
                navigate("/login");
              }}
            >
              Sign out
            </button>
          </div>
        </div>
      </header>

      <main className="flex-1 max-w-6xl w-full mx-auto px-6 py-8">
        <Outlet />
      </main>

      <footer className="border-t border-ink-700 py-6">
        <div className="max-w-6xl mx-auto px-6 text-xs text-mist-400 font-mono">
          © {new Date().getFullYear()} DQE
        </div>
      </footer>
    </div>
  );
}

export function LogoMark({ size = 28 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" fill="none">
      <rect x="1" y="1" width="30" height="30" rx="8" stroke="#3FD1C6" strokeWidth="1.5" />
      <circle cx="16" cy="16" r="7.5" stroke="#3FD1C6" strokeWidth="2" />
      <path
        d="M12.5 16.2l2.4 2.4 5-5.4"
        stroke="#3FD1C6"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
