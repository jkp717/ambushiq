import { createContext, useState, useEffect, useRef } from "react";
import { api, tokenStore } from "../services/api.js";

const AuthContext = createContext(null);

function AuthProvider({ children }) {
  const [authState, setAuthState] = useState("checking");
  const [authErr, setAuthErr] = useState("");
  const [version, setVersion] = useState("");
  // Mirrors authState for the event listener below, which is registered once
  // ([] deps) and would otherwise only ever see the initial "checking" value.
  const authStateRef = useRef(authState);
  useEffect(() => { authStateRef.current = authState; }, [authState]);

  useEffect(() => {
    api("/health").then((h) => {
      setVersion(h.version || "");
      if (!h.auth_required) { setAuthState("ok"); return; }
      api("/verify").then(() => setAuthState("ok")).catch(() => setAuthState("need"));
    }).catch(() => setAuthState("need"));
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
      setAuthErr("Your session expired — please sign in again.");
      setAuthState("need");
    }
    window.addEventListener("sa:unauthorized", onUnauthorized);
    return () => window.removeEventListener("sa:unauthorized", onUnauthorized);
  }, []);

  async function login(token) {
    tokenStore.set(token.trim());
    try { await api("/verify"); setAuthState("ok"); setAuthErr(""); }
    catch { tokenStore.clear(); setAuthErr("That access key didn't work."); }
  }

  function logout() {
    tokenStore.clear();
    setAuthState("need");
  }

  return (
    <AuthContext.Provider value={{ authState, authErr, version, login, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export { AuthContext, AuthProvider };
