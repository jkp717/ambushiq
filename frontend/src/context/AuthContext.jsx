import { createContext, useState, useEffect, useRef } from "react";
import { api, tokenStore } from "../services/api.js";

const AuthContext = createContext(null);

// Set after a successful sign-in check, so opening the app with no signal (when /health and /verify
// can't be reached) goes straight to the saved data instead of the sign-in screen. A real 401 still
// signs out.
const AUTH_OK_KEY = "sa_auth_ok";
const rememberOk = (ok) => { try { ok ? localStorage.setItem(AUTH_OK_KEY, "1") : localStorage.removeItem(AUTH_OK_KEY); } catch { /* ignore */ } };
const wasOk = () => { try { return localStorage.getItem(AUTH_OK_KEY) === "1"; } catch { return false; } };
const unreachable = (e) => e.code === undefined || e.code >= 500;

function AuthProvider({ children }) {
  const [authState, setAuthState] = useState("checking");
  const [authErr, setAuthErr] = useState("");
  const [version, setVersion] = useState("");
  // Mirrors authState for the event listener below, which is registered once
  // ([] deps) and would otherwise only ever see the initial "checking" value.
  const authStateRef = useRef(authState);
  useEffect(() => { authStateRef.current = authState; }, [authState]);

  useEffect(() => {
    const ok = () => { rememberOk(true); setAuthState("ok"); };
    const failed = (e) => setAuthState(unreachable(e) && wasOk() ? "ok" : "need");
    api("/health").then((h) => {
      setVersion(h.version || "");
      if (!h.auth_required) { ok(); return; }
      api("/verify").then(ok).catch(failed);
    }).catch(failed);
  }, []);

  // Any API call anywhere in the app that gets a 401 (e.g. APP_TOKEN rotated
  // server-side, or the stored token otherwise stops being valid) fires this —
  // without it the app just sits on broken pages with no way back to login.
  // Only acts while a session was actually established (authState "ok"); a
  // 401 during the initial /verify check above just means "not logged in
  // yet," which that check's own .catch() already handles — showing "session
  // expired" there too would be confusing on a first-ever visit.
  useEffect(() => {
    function onUnauthorized() {
      if (authStateRef.current !== "ok") return;
      tokenStore.clear();
      rememberOk(false);
      setAuthErr("Your session expired — please sign in again.");
      setAuthState("need");
    }
    window.addEventListener("sa:unauthorized", onUnauthorized);
    return () => window.removeEventListener("sa:unauthorized", onUnauthorized);
  }, []);

  async function login(token) {
    tokenStore.set(token.trim());
    try { await api("/verify"); rememberOk(true); setAuthState("ok"); setAuthErr(""); }
    catch { tokenStore.clear(); setAuthErr("That access key didn't work."); }
  }

  function logout() {
    tokenStore.clear();
    rememberOk(false);
    setAuthState("need");
  }

  return (
    <AuthContext.Provider value={{ authState, authErr, version, login, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export { AuthContext, AuthProvider };
