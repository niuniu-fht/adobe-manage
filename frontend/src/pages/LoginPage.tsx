import { KeyRound, ShieldCheck } from "lucide-react";
import { FormEvent, useState } from "react";
import { apiFetch, setCsrfToken } from "../lib/api";

export function LoginPage({ onLogin }: { onLogin: () => void }) {
  const [accessKey, setAccessKey] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setLoading(true);
    setError("");
    try {
      const result = await apiFetch<{ csrf_token: string }>("/auth/login", {
        method: "POST",
        body: JSON.stringify({ access_key: accessKey })
      });
      setCsrfToken(result.csrf_token);
      onLogin();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "登录失败");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="login-page">
      <section className="login-panel">
        <div className="login-mark"><ShieldCheck size={24} /></div>
        <div>
          <p className="eyebrow">ADOBE2API FLEET</p>
          <h1>运维中心</h1>
        </div>
        <form onSubmit={submit}>
          <label htmlFor="access-key">访问密钥</label>
          <div className="input-with-icon">
            <KeyRound size={17} />
            <input
              id="access-key"
              type="password"
              value={accessKey}
              onChange={(event) => setAccessKey(event.target.value)}
              autoFocus
              autoComplete="current-password"
              required
            />
          </div>
          {error && <p className="form-error">{error}</p>}
          <button className="primary-btn login-btn" type="submit" disabled={loading}>
            {loading ? "验证中..." : "进入控制台"}
          </button>
        </form>
      </section>
    </main>
  );
}
