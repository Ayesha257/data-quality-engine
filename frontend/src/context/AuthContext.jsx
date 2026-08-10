import { createContext, useContext, useEffect, useState, useCallback } from "react";
import { api, STORAGE_KEYS } from "../api/client.js";

const AuthContext = createContext(null);

/**
 * Real email + password accounts. The backend issues a JWT on
 * register/login (see phase2/api/auth.py) that encodes the user's
 * client_id -- so nothing else in the app needs to know or ask for a
 * "Client ID" anymore, it's fully automatic.
 */
export function AuthProvider({ children }) {
  const [token, setToken] = useState(() => localStorage.getItem(STORAGE_KEYS.token) || "");
  const [clientId, setClientId] = useState(() => localStorage.getItem(STORAGE_KEYS.clientId) || "");
  const [email, setEmail] = useState(() => localStorage.getItem(STORAGE_KEYS.email) || "");
  const [status, setStatus] = useState("idle"); // idle | checking | ready
  const [error, setError] = useState(null);

  const isAuthenticated = Boolean(token && clientId);

  const persist = (data) => {
    localStorage.setItem(STORAGE_KEYS.token, data.access_token);
    localStorage.setItem(STORAGE_KEYS.clientId, data.client_id);
    localStorage.setItem(STORAGE_KEYS.email, data.email);
    setToken(data.access_token);
    setClientId(data.client_id);
    setEmail(data.email);
  };

  const clear = () => {
    localStorage.removeItem(STORAGE_KEYS.token);
    localStorage.removeItem(STORAGE_KEYS.clientId);
    localStorage.removeItem(STORAGE_KEYS.email);
    setToken("");
    setClientId("");
    setEmail("");
  };

  const login = useCallback(async ({ email, password }) => {
    setError(null);
    setStatus("checking");
    try {
      const data = await api.login({ email, password });
      persist(data);
      setStatus("ready");
      return true;
    } catch (e) {
      setStatus("idle");
      setError(e.message);
      return false;
    }
  }, []);

  const register = useCallback(async ({ email, password, fullName }) => {
    setError(null);
    setStatus("checking");
    try {
      const data = await api.register({ email, password, fullName });
      persist(data);
      setStatus("ready");
      return true;
    } catch (e) {
      setStatus("idle");
      setError(e.message);
      return false;
    }
  }, []);

  const logout = useCallback(() => {
    clear();
    setStatus("idle");
  }, []);

  // On first load, trust stored credentials optimistically -- pages will
  // surface a 401 if the token expired, which sends the user back here.
  useEffect(() => {
    if (token && clientId) setStatus("ready");
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <AuthContext.Provider
      value={{ token, clientId, email, isAuthenticated, status, error, login, register, logout }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
