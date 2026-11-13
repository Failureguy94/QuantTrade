"use client";

import { useState } from "react";
import { toast } from "sonner";

import { EquityChart } from "@/components/charts/equity-chart";
import { Field, FormError, NativeSelect } from "@/components/field";
import { Stat, StatGrid } from "@/components/stat";
import { EmptyState, ErrorState, LoadingRows, Spinner, Warnings } from "@/components/states";
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
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useMoveCash, useRecordSnapshot, useSnapshots, useValuation } from "@/hooks/use-api";
import { errorMessage } from "@/lib/api/client";
import type { Portfolio } from "@/lib/api/types";
import {
  formatDate,
  formatMoney,
  formatNumber,
  formatPercent,
  signClass,
  toNumber,
} from "@/lib/format";

export function CashDialog({ portfolio, onClose }: { portfolio: Portfolio; onClose: () => void }) {
  const move = useMoveCash(portfolio.id);
  const [kind, setKind] = useState<"DEPOSIT" | "WITHDRAWAL">("DEPOSIT");
  const [amount, setAmount] = useState("");

  return (
    <Dialog open onOpenChange={(open) => (open ? null : onClose())}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Move cash</DialogTitle>
          <DialogDescription>
            Available: {formatMoney(portfolio.cash_balance, portfolio.base_currency)}
          </DialogDescription>
        </DialogHeader>
        <form
          className="space-y-4"
          onSubmit={(event) => {
            event.preventDefault();
            move.mutate(
              { kind, amount },
              {
                onSuccess: () => {
                  toast.success(kind === "DEPOSIT" ? "Deposit recorded" : "Withdrawal recorded");
                  onClose();
                },
              },
            );
          }}
        >
          <div className="grid grid-cols-2 gap-4">
            <Field label="Direction">
              {(props) => (
                <NativeSelect
                  {...props}
                  value={kind}
                  onChange={(event) => setKind(event.target.value as typeof kind)}
                >
                  <option value="DEPOSIT">Deposit</option>
                  <option value="WITHDRAWAL">Withdraw</option>
                </NativeSelect>
              )}
            </Field>
            <Field label={`Amount (${portfolio.base_currency})`}>
              {(props) => (
                <Input
                  {...props}
                  type="number"
                  min="0.01"
                  step="any"
                  required
                  value={amount}
                  onChange={(event) => setAmount(event.target.value)}
                />
              )}
            </Field>
          </div>
          <FormError message={move.error ? errorMessage(move.error) : null} />
          <DialogFooter>
            <Button type="button" variant="outline" onClick={onClose}>
              Cancel
            </Button>
            <Button type="submit" disabled={move.isPending || !amount}>
              {move.isPending ? <Spinner /> : null}
              Confirm
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

export function PortfolioOverview({ portfolio }: { portfolio: Portfolio }) {
  const valuation = useValuation(portfolio.id);
  const snapshots = useSnapshots(portfolio.id);
  const record = useRecordSnapshot(portfolio.id);
  const currency = portfolio.base_currency;

  if (valuation.isPending) return <LoadingRows />;
  if (valuation.isError) {
    return <ErrorState error={valuation.error} onRetry={() => valuation.refetch()} />;
  }
  const data = valuation.data;
  const equity = toNumber(data.total_equity) ?? 0;
  const initial = toNumber(portfolio.initial_capital) ?? 0;
  const history = snapshots.data ?? [];

  return (
    <div className="space-y-6">
      <Warnings items={data.warnings} />
      <StatGrid>
        <Stat
          label="Total equity"
          value={formatMoney(data.total_equity, currency)}
          hint={`As of ${formatDate(data.valued_at)}`}
        />
        <Stat label="Cash" value={formatMoney(data.cash, currency)} />
        <Stat label="Positions" value={formatMoney(data.positions_value, currency)} />
        <Stat
          label="Unrealised P&L"
          value={formatMoney(data.unrealized_pnl, currency, { sign: true })}
          valueClassName={signClass(data.unrealized_pnl)}
          hint={
            initial > 0
              ? `${formatPercent(equity / initial - 1, { sign: true })} vs starting cash`
              : undefined
          }
        />
      </StatGrid>

      <Card>
        <CardHeader>
          <CardTitle>Positions</CardTitle>
          <CardDescription>Valued at the latest daily reference price.</CardDescription>
        </CardHeader>
        <CardContent>
          {data.positions.length === 0 ? (
            <EmptyState
              title="No positions"
              description="Place a buy order on the Orders tab to open one."
            />
          ) : (
            <div className="overflow-x-auto rounded-lg border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Symbol</TableHead>
                    <TableHead className="text-right">Quantity</TableHead>
                    <TableHead className="text-right">Average price</TableHead>
                    <TableHead className="text-right">Price</TableHead>
                    <TableHead className="text-right">Value</TableHead>
                    <TableHead className="text-right">Unrealised P&amp;L</TableHead>
                    <TableHead className="text-right">Weight</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {data.positions.map((position) => (
                    <TableRow key={position.instrument_id}>
                      <TableCell className="font-medium">{position.symbol}</TableCell>
                      <TableCell className="text-right tabular-nums">
                        {formatNumber(position.quantity, 4)}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {formatMoney(position.average_price, currency)}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {formatMoney(position.market_price, currency)}
                        <span className="block text-xs text-muted-foreground">
                          {position.price_as_of ? formatDate(position.price_as_of) : "at cost"}
                        </span>
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {formatMoney(position.market_value, currency)}
                      </TableCell>
                      <TableCell
                        className={`text-right tabular-nums ${signClass(position.unrealized_pnl)}`}
                      >
                        {formatMoney(position.unrealized_pnl, currency, { sign: true })}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {formatPercent(
                          equity > 0 ? (toNumber(position.market_value) ?? 0) / equity : null,
                          { digits: 1 },
                        )}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Equity history</CardTitle>
          <CardDescription>
            Built from snapshots. Record one whenever you want a point on the chart.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <Button
            variant="outline"
            size="sm"
            disabled={record.isPending}
            onClick={() =>
              record.mutate(undefined, {
                onSuccess: () => toast.success("Snapshot recorded"),
                onError: (error) => toast.error(errorMessage(error)),
              })
            }
          >
            {record.isPending ? <Spinner /> : null}
            Record snapshot
          </Button>
          {history.length < 2 ? (
            <p className="text-sm text-muted-foreground">
              {history.length === 0 ? "No snapshots yet." : "One snapshot so far."} The chart
              appears once there are two.
            </p>
          ) : (
            <EquityChart
              currency={currency}
              baseline={initial}
              height={260}
              series={[
                {
                  name: "Equity",
                  points: history.map((snapshot) => ({
                    timestamp: snapshot.recorded_at,
                    value: Number(snapshot.total_equity),
                  })),
                },
              ]}
            />
          )}
        </CardContent>
      </Card>
    </div>
  );
}
