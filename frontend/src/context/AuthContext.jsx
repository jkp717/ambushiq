import { createContext, useState, useEffect } from "react";
import { api, tokenStore } from "../services/api.js";

const AuthContext = createContext(null);

function AuthProvider({ children }) {
  const [authState, setAuthState] = useState("checking");
  const [authErr, setAuthErr] = useState("");
  const [version, setVersion] = useState("");

  useEffect(() => {
    api("/health").then((h) => {
      setVersion(h.version || "");
      if (!h.auth_required) { setAuthState("ok"); return; }
      api("/verify").then(() => setAuthState("ok")).catch(() => setAuthState("need"));
    }).catch(() => setAuthState("need"));
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
