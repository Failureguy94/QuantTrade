"use client";

import Link from "next/link";
import { useState } from "react";

import { AuthCard } from "@/components/auth-card";
import { Field, FormError } from "@/components/field";
import { Spinner } from "@/components/states";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ApiError, api, errorMessage, sessionRequest, unwrap } from "@/lib/api/client";
import { leaveTo } from "@/lib/navigation";

export default function RegisterPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [awaitingVerification, setAwaitingVerification] = useState(false);

  const mismatch = confirm.length > 0 && confirm !== password;
  const tooShort = password.length > 0 && password.length < 8;

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (mismatch || tooShort) return;
    setPending(true);
    setError(null);
    try {
      await unwrap(api.POST("/api/v1/auth/register", { body: { email, password } }));
      try {
        await sessionRequest("login", { email, password });
        leaveTo("/dashboard");
      } catch (caught) {
        // The account exists; signing in just needs the email confirmed first.
        if (caught instanceof ApiError && caught.code === "email_not_verified") {
          setAwaitingVerification(true);
          setPending(false);
          return;
        }
        throw caught;
      }
    } catch (caught) {
      setError(errorMessage(caught));
      setPending(false);
    }
  }

  if (awaitingVerification) {
    return (
      <AuthCard
        title="Check your email"
        description={`We sent a confirmation link to ${email}. Open it to finish creating your account.`}
        footer={
          <Link href="/login" className="font-medium text-foreground underline-offset-4 hover:underline">
            Back to sign in
          </Link>
        }
      >
        <p className="text-sm text-muted-foreground">
          Nothing arrived?{" "}
          <Link href={`/verify-email?email=${encodeURIComponent(email)}`} className="underline">
            Send it again
          </Link>
          .
        </p>
      </AuthCard>
    );
  }

  return (
    <AuthCard
      title="Create your account"
      description="Backtest strategies and paper trade with simulated money."
      footer={
        <>
          Already have an account?{" "}
          <Link href="/login" className="font-medium text-foreground underline-offset-4 hover:underline">
            Sign in
          </Link>
        </>
      }
    >
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
        <Field
          label="Password"
          hint="At least 8 characters."
          error={tooShort ? "Use at least 8 characters." : null}
        >
          {(props) => (
            <Input
              {...props}
              type="password"
              autoComplete="new-password"
              required
              value={password}
              onChange={(event) => setPassword(event.target.value)}
            />
          )}
        </Field>
        <Field label="Confirm password" error={mismatch ? "The passwords do not match." : null}>
          {(props) => (
            <Input
              {...props}
              type="password"
              autoComplete="new-password"
              required
              value={confirm}
              onChange={(event) => setConfirm(event.target.value)}
            />
          )}
        </Field>
        <FormError message={error} />
        <Button
          type="submit"
          className="w-full"
          disabled={pending || !email || !password || !confirm || mismatch || tooShort}
        >
          {pending ? <Spinner /> : null}
          Create account
        </Button>
      </form>
    </AuthCard>
  );
}
