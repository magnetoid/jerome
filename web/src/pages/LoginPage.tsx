import { useState, type FormEvent } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { ApiError, apiFetch } from "@/lib/api";
import { ME_KEY, type Me } from "@/hooks/useMe";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

/** Only same-site paths: starts with one "/" and is not "//host" or "/\host". */
export function safeNext(next: string | null): string {
  if (next && next.startsWith("/") && !next.startsWith("//") && !next.startsWith("/\\")) {
    return next;
  }
  return "/";
}

function waitText(seconds: number | undefined): string {
  if (seconds === undefined) return "Too many attempts. Try again later.";
  if (seconds < 60) {
    const s = Math.max(1, Math.ceil(seconds));
    return `Too many attempts. Try again in ${s} ${s === 1 ? "second" : "seconds"}.`;
  }
  const m = Math.ceil(seconds / 60);
  return `Too many attempts. Try again in ${m} ${m === 1 ? "minute" : "minutes"}.`;
}

export default function LoginPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const navigate = useNavigate();
  const client = useQueryClient();
  const [params] = useSearchParams();

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setPending(true);
    try {
      const me = await apiFetch<Me>("/v1/auth/login", {
        method: "POST",
        json: { email, password },
      });
      client.setQueryData(ME_KEY, me);
      navigate(me.must_change_password ? "/change-password" : safeNext(params.get("next")), {
        replace: true,
      });
    } catch (err) {
      if (err instanceof ApiError && err.status === 429) setError(waitText(err.retryAfter));
      else if (err instanceof ApiError && err.status === 401) setError("Wrong email or password");
      else setError(err instanceof ApiError ? err.detail : "Could not sign in");
      setPending(false);
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center p-4">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle className="text-2xl">Jerome</CardTitle>
        </CardHeader>
        <CardContent>
          <form onSubmit={onSubmit} className="grid gap-4">
            <div className="grid gap-2">
              <Label htmlFor="login-email">Email</Label>
              <Input
                id="login-email"
                type="email"
                autoComplete="username"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
              />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="login-password">Password</Label>
              <Input
                id="login-password"
                type="password"
                autoComplete="current-password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
            </div>
            {error && (
              <p role="alert" className="text-sm text-destructive">
                {error}
              </p>
            )}
            <Button type="submit" disabled={pending}>
              {pending ? "Signing in…" : "Sign in"}
            </Button>
          </form>
        </CardContent>
      </Card>
    </main>
  );
}
