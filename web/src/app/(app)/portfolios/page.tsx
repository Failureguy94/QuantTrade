"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { Plus } from "lucide-react";
import { toast } from "sonner";

import { Field, FormError, NativeSelect } from "@/components/field";
import { PageHeader } from "@/components/page-header";
import { EmptyState, ErrorState, LoadingRows, Spinner } from "@/components/states";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useCreatePortfolio, useInstruments, usePortfolios } from "@/hooks/use-api";
import { errorMessage } from "@/lib/api/client";
import { formatDate, formatMoney } from "@/lib/format";

function CreatePortfolioDialog({ onClose }: { onClose: () => void }) {
  const router = useRouter();
  const create = useCreatePortfolio();
  const instruments = useInstruments();
  const [name, setName] = useState("");
  const [currency, setCurrency] = useState("USD");
  const [capital, setCapital] = useState("100000");

  // A portfolio trades instruments in its own currency, so offer the ones that exist.
  const currencies = [
    ...new Set(["USD", ...(instruments.data?.items ?? []).map((item) => item.currency_code)]),
  ].sort();

  return (
    <Dialog open onOpenChange={(open) => (open ? null : onClose())}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>New paper portfolio</DialogTitle>
          <DialogDescription>
            Simulated money only. A portfolio trades instruments priced in its own currency.
          </DialogDescription>
        </DialogHeader>
        <form
          className="space-y-4"
          onSubmit={(event) => {
            event.preventDefault();
            create.mutate(
              { name, base_currency: currency, initial_capital: capital },
              {
                onSuccess: (portfolio) => {
                  toast.success(`Opened ${portfolio.name}`);
                  router.push(`/portfolios/${portfolio.id}`);
                },
              },
            );
          }}
        >
          <Field label="Name">
            {(props) => (
              <Input
                {...props}
                required
                maxLength={150}
                value={name}
                onChange={(event) => setName(event.target.value)}
              />
            )}
          </Field>
          <div className="grid grid-cols-2 gap-4">
            <Field label="Currency">
              {(props) => (
                <NativeSelect
                  {...props}
                  value={currency}
                  onChange={(event) => setCurrency(event.target.value)}
                >
                  {currencies.map((code) => (
                    <option key={code} value={code}>
                      {code}
                    </option>
                  ))}
                </NativeSelect>
              )}
            </Field>
            <Field label="Starting cash">
              {(props) => (
                <Input
                  {...props}
                  type="number"
                  min="0.01"
                  step="any"
                  required
                  value={capital}
                  onChange={(event) => setCapital(event.target.value)}
                />
              )}
            </Field>
          </div>
          <FormError message={create.error ? errorMessage(create.error) : null} />
          <DialogFooter>
            <Button type="button" variant="outline" onClick={onClose}>
              Cancel
            </Button>
            <Button type="submit" disabled={create.isPending || !name.trim() || !capital}>
              {create.isPending ? <Spinner /> : null}
              Open portfolio
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

export default function PortfoliosPage() {
  const portfolios = usePortfolios();
  const [creating, setCreating] = useState(false);

  return (
    <>
      <PageHeader
        title="Portfolios"
        description="Paper accounts: place orders at the latest prices with simulated money."
        actions={
          <Button onClick={() => setCreating(true)}>
            <Plus className="size-4" aria-hidden />
            New portfolio
          </Button>
        }
      />
      {portfolios.isPending ? (
        <LoadingRows />
      ) : portfolios.isError ? (
        <ErrorState error={portfolios.error} onRetry={() => portfolios.refetch()} />
      ) : portfolios.data.items.length === 0 ? (
        <EmptyState
          title="No portfolios yet"
          description="Open a paper portfolio to try orders without risking anything."
          action={<Button onClick={() => setCreating(true)}>New portfolio</Button>}
        />
      ) : (
        <div className="rounded-lg border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Name</TableHead>
                <TableHead>Currency</TableHead>
                <TableHead className="text-right">Starting cash</TableHead>
                <TableHead className="text-right">Cash now</TableHead>
                <TableHead>Opened</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {portfolios.data.items.map((portfolio) => (
                <TableRow key={portfolio.id}>
                  <TableCell className="font-medium">
                    <Link href={`/portfolios/${portfolio.id}`} className="hover:underline">
                      {portfolio.name}
                    </Link>
                  </TableCell>
                  <TableCell>{portfolio.base_currency}</TableCell>
                  <TableCell className="text-right tabular-nums">
                    {formatMoney(portfolio.initial_capital, portfolio.base_currency)}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {formatMoney(portfolio.cash_balance, portfolio.base_currency)}
                  </TableCell>
                  <TableCell>{formatDate(portfolio.created_at)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
      {creating ? <CreatePortfolioDialog onClose={() => setCreating(false)} /> : null}
    </>
  );
}
