import { useState, type FormEvent } from "react";
import { api, setToken } from "../api/generated";
import { Alert, Button, Card, Field } from "../components/ui";
import { useAction } from "../lib/useAction";

export default function AccountPage({ onLoggedIn }: { onLoggedIn: (username: string) => void }) {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [invalidMsg, setInvalidMsg] = useState<string | null>(null);
  const auth = useAction((body: { username: string; password: string }) =>
    mode === "login" ? api.postLogin(body) : api.postRegister(body));
  const invalid = username.trim().length < 3 || password.length < 6;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setInvalidMsg(invalid ? "Username needs 3+ characters and password 6+." : null);
    if (invalid) return;
    const session = await auth.run({ username: username.trim(), password });
    if (session) {
      setToken(session.token);
      onLoggedIn(session.username);
    }
  };
  const error = invalidMsg ?? auth.error;

  return (
    <Card title={mode === "login" ? "Log in" : "Create an account"}>
      <form onSubmit={submit} noValidate>
        <Field label="Username" value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" />
        <Field label="Password" type="password" value={password} onChange={(e) => setPassword(e.target.value)}
          autoComplete={mode === "login" ? "current-password" : "new-password"} hint="At least 6 characters" />
        {error && <Alert>{error}</Alert>}
        <div className="row">
          <Button type="submit" loading={auth.pending}>{mode === "login" ? "Log in" : "Sign up"}</Button>
          <Button variant="ghost" onClick={() => { setMode(mode === "login" ? "register" : "login"); setInvalidMsg(null); auth.reset(); }}>
            {mode === "login" ? "Need an account? Sign up" : "Have an account? Log in"}
          </Button>
        </div>
      </form>
    </Card>
  );
}
