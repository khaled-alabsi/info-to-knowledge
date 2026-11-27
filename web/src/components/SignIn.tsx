import { useState } from "react";
import { signIn } from "../api";
import type { Account } from "../api";
import Icon from "./Icon";

// The sign-in a server with accounts asks for before anything else.
export default function SignIn({ onSignedIn }: { onSignedIn: (account: Account) => void }) {
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [problem, setProblem] = useState("");
  const [isBusy, setIsBusy] = useState(false);

  const submit = async () => {
    setIsBusy(true);
    setProblem("");
    try {
      onSignedIn(await signIn(name, password));
    } catch (failure) {
      // the server's reason, without its status number
      setProblem((failure as Error).message.replace(/^\d+: /, ""));
      setPassword("");
    } finally {
      setIsBusy(false);
    }
  };

  return (
    <div className="sign-in">
      <form className="sign-in-box" onSubmit={(event) => { event.preventDefault(); void submit(); }}>
        <h1><span className="logo">i2k</span> Sign in</h1>
        <label>Name<input autoFocus autoComplete="username" value={name} onChange={(event) => setName(event.target.value)} /></label>
        <label>Password<input type="password" autoComplete="current-password" value={password}
          onChange={(event) => setPassword(event.target.value)} /></label>
        {problem && <p className="sign-in-problem" role="alert">{problem}</p>}
        <button className="btn primary" type="submit" disabled={isBusy || !name || !password}>
          <Icon name="unlock" /> {isBusy ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </div>
  );
}
