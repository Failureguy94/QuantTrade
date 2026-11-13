"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { AuthCard } from "@/components/auth-card";
import { Field, FormError } from "@/components/field";
import { Spinner } from "@/components/states";
import { Button, buttonVariants } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api, errorMessage, unwrap } from "@/lib/api/client";

type State = "idle" | "verifying" | "verified" | "failed";

export default function VerifyEmailPage() {
  const params = useSearchParams();
  const token = params.get("token");
  const [state, setState] = useState<State>(token ? "verifying" : "idle");
  const [error, setError] = useState<string | null>(null);
  const [email, setEmail] = useState(params.get("email") ?? "");
  const [resent, setResent] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const started = useRef(false);

  useEffect(() => {
    // The link is single-use, so it must be submitted exactly once.
    if (!token || started.current) return;
    started.current = true;
    unwrap(api.POST("/api/v1/auth/verify-email", { body: { token } }))
      .then(() => setState("verified"))
      .catch((caught) => {
        setError(errorMessage(caught));
        setState("failed");
      });
  }, [token]);

  async function resend(event: React.FormEvent) {
    event.preventDefault();
    setPending(true);
    setError(null);
    try {
      const result = await unwrap(api.POST("/api/v1/auth/resend-verification", { body: { email } }));
      setResent(result.detail);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setPending(false);
    }
  }

  if (state === "verifying") {
    return (
      <AuthCard title="Confirming your email">
        <div className="flex items-center gap-2 text-sm text-muted-foreground" role="status">
          <Spinner /> One moment…
        </div>
      </AuthCard>
    );
  }

  if (state === "verified") {
    return (
      <AuthCard title="Email confirmed" description="Your account is ready.">
        <Link href="/login" className={buttonVariants({ className: "w-full" })}>
          Continue to sign in
        </Link>
      </AuthCard>
    );
  }

  return (
    <AuthCard
      title={state === "failed" ? "This link did not work" : "Confirm your email"}
      description="Enter your email address and we will send a new confirmation link."
      footer={
        <Link href="/login" className="font-medium text-foreground underline-offset-4 hover:underline">
          Back to sign in
        </Link>
      }
    >
      {state === "failed" ? <FormError message={error} /> : null}
      {resent ? (
        <p role="status" className="rounded-lg border border-emerald-500/40 bg-emerald-500/10 p-3 text-sm">
          {resent}
        </p>
      ) : (
        <form onSubmit={resend} className="space-y-4" noValidate>
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
          {state !== "failed" ? <FormError message={error} /> : null}
          <Button type="submit" className="w-full" disabled={pending || !email}>
            {pending ? <Spinner /> : null}
            Send confirmation link
          </Button>
        </form>
      )}
    </AuthCard>
  );
}
