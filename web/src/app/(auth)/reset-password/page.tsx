"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useState } from "react";

import { AuthCard } from "@/components/auth-card";
import { Field, FormError } from "@/components/field";
import { Spinner } from "@/components/states";
import { Button, buttonVariants } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api, errorMessage, unwrap } from "@/lib/api/client";
import { leaveTo } from "@/lib/navigation";

export default function ResetPasswordPage() {
  const token = useSearchParams().get("token");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  const mismatch = confirm.length > 0 && confirm !== password;
  const tooShort = password.length > 0 && password.length < 8;

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!token || mismatch || tooShort) return;
    setPending(true);
    setError(null);
    try {
      await unwrap(
        api.POST("/api/v1/auth/reset-password", { body: { token, new_password: password } }),
      );
      leaveTo("/login?reset=done");
    } catch (caught) {
      setError(errorMessage(caught));
      setPending(false);
    }
  }

  if (!token) {
    return (
      <AuthCard
        title="This link is incomplete"
        description="Open the link from your email again, or ask for a new one."
      >
        <Link href="/forgot-password" className={buttonVariants({ className: "w-full" })}>
          Request a new link
        </Link>
      </AuthCard>
    );
  }

  return (
    <AuthCard title="Choose a new password" description="This signs you out everywhere else.">
      <form onSubmit={submit} className="space-y-4" noValidate>
        <Field
          label="New password"
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
        <Field label="Confirm new password" error={mismatch ? "The passwords do not match." : null}>
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
          disabled={pending || !password || !confirm || mismatch || tooShort}
        >
          {pending ? <Spinner /> : null}
          Change password
        </Button>
      </form>
      {error ? (
        <p className="text-center text-sm">
          <Link href="/forgot-password" className="underline">
            Request a new link
          </Link>
        </p>
      ) : null}
    </AuthCard>
  );
}
