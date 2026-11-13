"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useState } from "react";

import { AuthCard } from "@/components/auth-card";
import { Field, FormError } from "@/components/field";
import { Spinner } from "@/components/states";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ApiError, errorMessage, sessionRequest } from "@/lib/api/client";
import { leaveTo } from "@/lib/navigation";

/** Only follow `next` to a path on this site. */
function safeNext(next: string | null): string {
  return next && next.startsWith("/") && !next.startsWith("//") ? next : "/dashboard";
}

export default function LoginPage() {
  const params = useSearchParams();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [needsVerification, setNeedsVerification] = useState(false);
  const [pending, setPending] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setPending(true);
    setError(null);
    setNeedsVerification(false);
    try {
      await sessionRequest("login", { email, password });
      // A full navigation, so the new session cookie is used from the first request.
      leaveTo(safeNext(params.get("next")));
    } catch (caught) {
      setNeedsVerification(caught instanceof ApiError && caught.code === "email_not_verified");
      setError(errorMessage(caught));
      setPending(false);
    }
  }

  return (
    <AuthCard
      title="Sign in"
      description="Welcome back."
      footer={
        <>
          New here?{" "}
          <Link href="/register" className="font-medium text-foreground underline-offset-4 hover:underline">
            Create an account
          </Link>
        </>
      }
    >
      {params.get("reset") === "done" ? (
        <p role="status" className="rounded-lg border border-emerald-500/40 bg-emerald-500/10 p-3 text-sm">
          Your password was changed. Sign in with the new one.
        </p>
      ) : null}
      <form onSubmit={submit} className="space-y-4" noValidate>
        <Field label="Email">
          {(props) => (
            <Input
              {...props}
              type="email"
              autoComplete="email"
              required
              value={email}
              onChange={(event) => setEmail(event.target.value)}
            />
          )}
        </Field>
        <Field label="Password">
          {(props) => (
            <Input
              {...props}
              type="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(event) => setPassword(event.target.value)}
            />
          )}
        </Field>
        <FormError message={error} />
        {needsVerification ? (
          <p className="text-sm">
            <Link href={`/verify-email?email=${encodeURIComponent(email)}`} className="underline">
              Send the confirmation link again
            </Link>
          </p>
        ) : null}
        <Button type="submit" className="w-full" disabled={pending || !email || !password}>
          {pending ? <Spinner /> : null}
          Sign in
        </Button>
      </form>
      <p className="text-center text-sm">
        <Link href="/forgot-password" className="text-muted-foreground underline-offset-4 hover:underline">
          Forgot your password?
        </Link>
      </p>
    </AuthCard>
  );
}
