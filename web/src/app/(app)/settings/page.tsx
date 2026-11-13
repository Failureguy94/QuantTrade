"use client";

import { useState } from "react";
import { toast } from "sonner";

import { Field, FormError } from "@/components/field";
import { PageHeader } from "@/components/page-header";
import { ErrorState, LoadingRows, Spinner } from "@/components/states";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { useMe } from "@/hooks/use-api";
import { api, errorMessage, sessionRequest, unwrap } from "@/lib/api/client";
import { formatDate } from "@/lib/format";
import { leaveTo } from "@/lib/navigation";

function ChangePassword() {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  const mismatch = confirm.length > 0 && confirm !== next;
  const tooShort = next.length > 0 && next.length < 8;

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (mismatch || tooShort) return;
    setPending(true);
    setError(null);
    try {
      await sessionRequest("change-password", { current_password: current, new_password: next });
      toast.success("Password changed. Other devices were signed out.");
      setCurrent("");
      setNext("");
      setConfirm("");
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setPending(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Password</CardTitle>
        <CardDescription>Changing it signs you out on every other device.</CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={submit} className="max-w-sm space-y-4" noValidate>
          <Field label="Current password">
            {(props) => (
              <Input
                {...props}
                type="password"
                autoComplete="current-password"
                required
                value={current}
                onChange={(event) => setCurrent(event.target.value)}
              />
            )}
          </Field>
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
                value={next}
                onChange={(event) => setNext(event.target.value)}
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
            disabled={pending || !current || !next || !confirm || mismatch || tooShort}
          >
            {pending ? <Spinner /> : null}
            Change password
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}

function DeleteAccount() {
  const [open, setOpen] = useState(false);
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setPending(true);
    setError(null);
    try {
      await sessionRequest("delete-account", { password });
      leaveTo("/");
    } catch (caught) {
      setError(errorMessage(caught));
      setPending(false);
    }
  }

  return (
    <Card className="border-destructive/40">
      <CardHeader>
        <CardTitle>Delete account</CardTitle>
        <CardDescription>
          Permanently removes your strategies, backtests, portfolios, orders, ledger and risk
          reports. This cannot be undone.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <Button variant="destructive" onClick={() => setOpen(true)}>
          Delete my account
        </Button>
      </CardContent>
      <Dialog
        open={open}
        onOpenChange={(next) => {
          setOpen(next);
          if (!next) {
            setPassword("");
            setError(null);
          }
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete your account?</DialogTitle>
            <DialogDescription>
              Everything you created will be erased immediately. Enter your password to confirm.
            </DialogDescription>
          </DialogHeader>
          <form onSubmit={submit} className="space-y-4">
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
            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => setOpen(false)}>
                Keep my account
              </Button>
              <Button type="submit" variant="destructive" disabled={pending || !password}>
                {pending ? <Spinner /> : null}
                Delete everything
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </Card>
  );
}

export default function SettingsPage() {
  const me = useMe();
  const [resent, setResent] = useState(false);
  const [signingOut, setSigningOut] = useState(false);

  if (me.isPending) return <LoadingRows />;
  if (me.isError) return <ErrorState error={me.error} onRetry={() => me.refetch()} />;
  const user = me.data;

  async function resendVerification() {
    try {
      await unwrap(api.POST("/api/v1/auth/resend-verification", { body: { email: user.email } }));
      setResent(true);
      toast.success("Confirmation link sent");
    } catch (caught) {
      toast.error(errorMessage(caught));
    }
  }

  async function signOutEverywhere() {
    setSigningOut(true);
    try {
      await unwrap(api.POST("/api/v1/auth/logout-all"));
      await sessionRequest("logout");
      leaveTo("/login");
    } catch (caught) {
      toast.error(errorMessage(caught));
      setSigningOut(false);
    }
  }

  return (
    <>
      <PageHeader title="Settings" />
      <div className="space-y-6">
        <Card>
          <CardHeader>
            <CardTitle>Account</CardTitle>
            <CardDescription>Member since {formatDate(user.created_at)}</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <p className="flex flex-wrap items-center gap-2 text-sm">
              <span className="font-medium">{user.email}</span>
              {user.email_verified ? (
                <Badge variant="secondary">confirmed</Badge>
              ) : (
                <>
                  <Badge variant="secondary">not confirmed</Badge>
                  <Button variant="outline" size="sm" disabled={resent} onClick={resendVerification}>
                    {resent ? "Link sent" : "Send confirmation link"}
                  </Button>
                </>
              )}
            </p>
            <Button variant="outline" disabled={signingOut} onClick={signOutEverywhere}>
              {signingOut ? <Spinner /> : null}
              Sign out on all devices
            </Button>
          </CardContent>
        </Card>
        <ChangePassword />
        <DeleteAccount />
      </div>
    </>
  );
}
