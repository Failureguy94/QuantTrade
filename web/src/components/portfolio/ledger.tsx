"use client";

import { CheckCircle2, XCircle } from "lucide-react";

import { EmptyState, ErrorState, LoadingRows } from "@/components/states";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useInstruments, useLedger, useLedgerBalances, useTrades } from "@/hooks/use-api";
import type { Portfolio } from "@/lib/api/types";
import { formatDateTime, formatMoney, formatNumber, humanize, toNumber } from "@/lib/format";

function Check({ ok, children }: { ok: boolean; children: React.ReactNode }) {
  const Icon = ok ? CheckCircle2 : XCircle;
  return (
    <p className="flex items-center gap-2 text-sm">
      <Icon className={`size-4 ${ok ? "text-emerald-600" : "text-destructive"}`} aria-hidden />
      {children}
    </p>
  );
}

export function PortfolioTrades({ portfolio }: { portfolio: Portfolio }) {
  const trades = useTrades(portfolio.id);
  const instruments = useInstruments();
  const currency = portfolio.base_currency;
  const symbols = new Map(
    (instruments.data?.items ?? []).map((instrument) => [instrument.id, instrument.symbol]),
  );

  if (trades.isPending) return <LoadingRows />;
  if (trades.isError) return <ErrorState error={trades.error} onRetry={() => trades.refetch()} />;
  if (trades.data.items.length === 0) {
    return <EmptyState title="No fills yet" description="Fills appear here when orders execute." />;
  }
  return (
    <div className="overflow-x-auto rounded-lg border">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Executed</TableHead>
            <TableHead>Trade</TableHead>
            <TableHead className="text-right">Quantity</TableHead>
            <TableHead className="text-right">Price</TableHead>
            <TableHead className="text-right">Value</TableHead>
            <TableHead className="text-right">Commission</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {trades.data.items.map((trade) => (
            <TableRow key={trade.id}>
              <TableCell className="whitespace-nowrap">{formatDateTime(trade.executed_at)}</TableCell>
              <TableCell className="font-medium">
                {humanize(trade.side)} {symbols.get(trade.instrument_id) ?? "…"}
              </TableCell>
              <TableCell className="text-right tabular-nums">
                {formatNumber(trade.quantity, 4)}
              </TableCell>
              <TableCell className="text-right tabular-nums">
                {formatMoney(trade.fill_price, currency)}
              </TableCell>
              <TableCell className="text-right tabular-nums">
                {formatMoney(
                  (toNumber(trade.quantity) ?? 0) * (toNumber(trade.fill_price) ?? 0),
                  currency,
                )}
              </TableCell>
              <TableCell className="text-right tabular-nums">
                {formatMoney(trade.commission, currency)}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

export function PortfolioLedger({ portfolio }: { portfolio: Portfolio }) {
  const balances = useLedgerBalances(portfolio.id);
  const journal = useLedger(portfolio.id);
  const currency = portfolio.base_currency;

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>Balances</CardTitle>
          <CardDescription>
            Every cash movement and fill is posted as a balanced double entry. Debits are positive,
            credits negative.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {balances.isPending ? (
            <LoadingRows rows={3} />
          ) : balances.isError ? (
            <ErrorState error={balances.error} onRetry={() => balances.refetch()} />
          ) : (
            <div className="grid gap-6 md:grid-cols-2">
              <dl className="text-sm">
                {Object.entries(balances.data.balances as Record<string, string>).map(
                  ([account, amount]) => (
                    <div key={account} className="flex justify-between border-b py-2 last:border-b-0">
                      <dt className="text-muted-foreground">{humanize(account)}</dt>
                      <dd className="tabular-nums">{formatMoney(amount, currency)}</dd>
                    </div>
                  ),
                )}
              </dl>
              <div className="space-y-2">
                <Check ok={balances.data.is_balanced}>
                  {balances.data.is_balanced
                    ? "Debits and credits sum to zero"
                    : "Debits and credits do not sum to zero"}
                </Check>
                <Check ok={balances.data.is_reconciled}>
                  {balances.data.is_reconciled
                    ? "Ledger matches the portfolio's cash and positions"
                    : `Ledger differs from the portfolio: cash by ${formatMoney(balances.data.cash_difference, currency)}, positions by ${formatMoney(balances.data.securities_difference, currency)}`}
                </Check>
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Journal</CardTitle>
          <CardDescription>Newest first.</CardDescription>
        </CardHeader>
        <CardContent>
          {journal.isPending ? (
            <LoadingRows />
          ) : journal.isError ? (
            <ErrorState error={journal.error} onRetry={() => journal.refetch()} />
          ) : journal.data.items.length === 0 ? (
            <EmptyState title="No ledger entries yet" />
          ) : (
            <div className="overflow-x-auto rounded-lg border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>#</TableHead>
                    <TableHead>When</TableHead>
                    <TableHead>Transaction</TableHead>
                    <TableHead>Account</TableHead>
                    <TableHead className="text-right">Debit</TableHead>
                    <TableHead className="text-right">Credit</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {journal.data.items.flatMap((transaction) =>
                    transaction.entries.map((entry, index) => {
                      const amount = toNumber(entry.amount) ?? 0;
                      return (
                        <TableRow key={`${transaction.id}-${entry.line_number}`}>
                          <TableCell className="text-muted-foreground">
                            {index === 0 ? transaction.sequence : ""}
                          </TableCell>
                          <TableCell className="whitespace-nowrap">
                            {index === 0 ? formatDateTime(transaction.occurred_at) : ""}
                          </TableCell>
                          <TableCell>
                            {index === 0 ? (
                              <>
                                <span className="font-medium">{humanize(transaction.kind)}</span>
                                {transaction.description ? (
                                  <span className="block text-xs text-muted-foreground">
                                    {transaction.description}
                                  </span>
                                ) : null}
                              </>
                            ) : null}
                          </TableCell>
                          <TableCell>{humanize(entry.account)}</TableCell>
                          <TableCell className="text-right tabular-nums">
                            {amount > 0 ? formatMoney(amount, currency) : ""}
                          </TableCell>
                          <TableCell className="text-right tabular-nums">
                            {amount < 0 ? formatMoney(-amount, currency) : ""}
                          </TableCell>
                        </TableRow>
                      );
                    }),
                  )}
                </TableBody>
              </Table>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
