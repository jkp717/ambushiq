import { useState } from "react";
import { Wind, Lock } from "lucide-react";
import { useAuth } from "../hooks/useAuth.js";
import Centered from "../components/ui/Centered.jsx";

function Login() {
  const { authErr, login } = useAuth();
  const [tokenInput, setTokenInput] = useState("");

  return (
    <Centered>
      <div className="login-card">
        <div className="login-brand">
          <Wind size={28} color="var(--navy)" />
          <h1>AmbushIQ</h1>
        </div>
        <div className="login-field">
          <Lock size={15} color="var(--sub)" />
          <input type="password" value={tokenInput} placeholder="Enter access key"
            onChange={(e) => setTokenInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && login(tokenInput)} />
        </div>
        {authErr && <div className="login-err">{authErr}</div>}
        <button className="btn btn-primary login-btn" onClick={() => login(tokenInput)}>Unlock</button>
      </div>
    </Centered>
  );
}

export default Login;
