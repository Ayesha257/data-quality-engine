import { Link } from "react-router-dom";

export default function NotFoundPage() {
  return (
    <div className="min-h-[60vh] flex flex-col items-center justify-center text-center">
      <p className="font-mono text-teal-400 text-sm mb-2">404</p>
      <h1 className="font-display text-2xl font-semibold text-mist-100">Page not found</h1>
      <p className="text-sm text-mist-400 mt-1">That route doesn't exist.</p>
      <Link to="/upload" className="btn-primary mt-6">
        Back to console
      </Link>
    </div>
  );
}
