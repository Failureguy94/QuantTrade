"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { toast } from "sonner";

import { DrawdownChart, EquityChart } from "@/components/charts/equity-chart";
import { PageHeader } from "@/components/page-header";
import { ParameterList } from "@/components/parameter-form";
import { Stat, StatGrid } from "@/components/stat";
import { ErrorState, LoadingRows, Spinner, Warnings } from "@/components/states";
import { StatusBadge } from "@/components/status-badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useDeleteExperiment, useExperiment } from "@/hooks/use-api";
import { errorMessage } from "@/lib/api/client";
import { backtestConfiguration, backtestResults } from "@/lib/api/types";
import {
  formatDate,
  formatDateTime,
  formatMoney,
  formatNumber,
  formatPercent,
  formatRatio,
  humanize,
  signClass,
  strategyLabel,
  toNumber,
} from "@/lib/format";

function Detail({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex justify-between gap-4 border-b py-2 text-sm last:border-b-0">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="text-right">{children}</dd>
    </div>
  );
}

export default function BacktestPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const experiment = useExperiment(id);
  const remove = useDeleteExperiment();

  if (experiment.isPending) return <LoadingRows rows={6} />;
  if (experiment.isError) {
    return <ErrorState error={experiment.error} onRetry={() => experiment.refetch()} />;
  }

  const data = experiment.data;
  const config = backtestConfiguration(data);
  const results = backtestResults(data);
  const currency = config.instrument.currency;
  const waiting = data.status === "QUEUED" || data.status === "RUNNING";
  const title = `${strategyLabel(config.strategy.type)} v${config.strategy.version} on ${config.instrument.symbol}`;

  return (
    <>
      <PageHeader
        back={{ href: "/backtests", label: "Backtests" }}
        title={title}
        description={
          <span className="flex flex-wrap items-center gap-2">
            <StatusBadge status={data.status} />
            {formatDate(config.request.start)} – {formatDate(config.request.end)}
          </span>
        }
        actions={
          <>
            {data.status === "COMPLETED" ? (
              <Link
                href={`/risk?experiment=${data.id}`}
                className={buttonVariants({ variant: "outline" })}
              >
                Measure risk
              </Link>
            ) : null}
            <Link
              href={`/backtests/new?strategy=${config.strategy.id}&version=${data.strategy_version_id}&instrument=${config.instrument.id}`}
              className={buttonVariants({ variant: "outline" })}
            >
              Run again
            </Link>
            <Button
              variant="destructive"
              disabled={remove.isPending}
              onClick={() => {
                if (!window.confirm("Delete this backtest? This cannot be undone.")) return;
                remove.mutate(data.id, {
                  onSuccess: () => {
                    toast.success("Backtest deleted");
                    router.push("/backtests");
                  },
                  onError: (error) => toast.error(errorMessage(error)),
                });
              }}
            >
              Delete
            </Button>
          </>
        }
      />

      {waiting ? (
        <Card>
          <CardContent className="flex items-center gap-3 py-10" role="status">
            <Spinner className="size-5" />
            <div>
              <p className="font-medium">
                {data.status === "QUEUED" ? "Waiting to start…" : "Running…"}
              </p>
              <p className="text-sm text-muted-foreground">
                This page updates by itself. You can leave and come back.
              </p>
            </div>
          </CardContent>
        </Card>
      ) : null}

      {data.status === "FAILED" ? (
        <Card className="border-destructive/40">
          <CardHeader>
            <CardTitle>This run did not complete</CardTitle>
            <CardDescription>{data.error_message ?? "No reason was recorded."}</CardDescription>
          </CardHeader>
        </Card>
      ) : null}

      {results ? (
        <div className="space-y-6">
          <Warnings items={results.warnings} />
          <StatGrid>
            <Stat
              label="Total return"
              value={formatPercent(results.metrics.total_return, { sign: true })}
              valueClassName={signClass(results.metrics.total_return)}
              hint={`Buy and hold: ${formatPercent(results.metrics.benchmark_buy_and_hold_return, { sign: true })}`}
            />
            <Stat
              label="Final equity"
              value={formatMoney(results.final_equity, currency)}
              hint={`From ${formatMoney(config.request.initial_capital, currency)}`}
            />
            <Stat
              label="Sharpe ratio"
              value={formatRatio(results.metrics.sharpe_ratio)}
              hint={`Sortino ${formatRatio(results.metrics.sortino_ratio)}`}
            />
            <Stat
              label="Max drawdown"
              value={formatPercent(results.metrics.max_drawdown)}
              hint={`${results.metrics.max_drawdown_duration_bars ?? "—"} bars under water`}
            />
            <Stat label="Annual return" value={formatPercent(results.metrics.cagr, { sign: true })} />
            <Stat
              label="Annual volatility"
              value={formatPercent(results.metrics.annualized_volatility)}
            />
            <Stat
              label="Closed trades"
              value={results.metrics.closed_trades}
              hint={`Win rate ${formatPercent(results.metrics.win_rate, { digits: 0 })}`}
            />
            <Stat
              label="Time in market"
              value={formatPercent(results.metrics.exposure, { digits: 0 })}
              hint={`Profit factor ${formatRatio(results.metrics.profit_factor)}`}
            />
          </StatGrid>

          <Card>
            <CardHeader>
              <CardTitle>Equity</CardTitle>
              <CardDescription>The dashed line is the starting capital.</CardDescription>
            </CardHeader>
            <CardContent>
              <EquityChart
                currency={currency}
                baseline={toNumber(config.request.initial_capital) ?? undefined}
                series={[
                  {
                    name: "Equity",
                    points: results.equity_curve.map((point) => ({
                      timestamp: point.timestamp,
                      value: Number(point.equity),
                    })),
                  },
                ]}
              />
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Drawdown</CardTitle>
              <CardDescription>How far equity was below its previous peak.</CardDescription>
            </CardHeader>
            <CardContent>
              <DrawdownChart
                points={results.equity_curve.map((point) => ({
                  timestamp: point.timestamp,
                  value: Number(point.equity),
                }))}
              />
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Trades</CardTitle>
              <CardDescription>
                {results.trades.length} round trips · {formatMoney(results.total_commission, currency)}{" "}
                commission · {formatMoney(results.total_slippage, currency)} slippage
              </CardDescription>
            </CardHeader>
            <CardContent>
              {results.trades.length === 0 ? (
                <p className="text-sm text-muted-foreground">The strategy never traded.</p>
              ) : (
                <div
                  tabIndex={0}
                  role="region"
                  aria-label="Trades"
                  className="max-h-96 overflow-auto rounded-lg border outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
                >
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>Side</TableHead>
                        <TableHead>Entered</TableHead>
                        <TableHead>Exited</TableHead>
                        <TableHead className="text-right">Quantity</TableHead>
                        <TableHead className="text-right">Entry</TableHead>
                        <TableHead className="text-right">Exit</TableHead>
                        <TableHead className="text-right">P&amp;L</TableHead>
                        <TableHead className="text-right">Return</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {results.trades.map((trade, index) => (
                        <TableRow key={`${trade.entry_time}-${index}`}>
                          <TableCell>{humanize(trade.side)}</TableCell>
                          <TableCell>{formatDate(trade.entry_time)}</TableCell>
                          <TableCell>{trade.is_open ? "Open" : formatDate(trade.exit_time)}</TableCell>
                          <TableCell className="text-right tabular-nums">
                            {formatNumber(trade.quantity, 4)}
                          </TableCell>
                          <TableCell className="text-right tabular-nums">
                            {formatMoney(trade.entry_price, currency)}
                          </TableCell>
                          <TableCell className="text-right tabular-nums">
                            {formatMoney(trade.exit_price, currency)}
                          </TableCell>
                          <TableCell className={`text-right tabular-nums ${signClass(trade.pnl)}`}>
                            {formatMoney(trade.pnl, currency, { sign: true })}
                          </TableCell>
                          <TableCell
                            className={`text-right tabular-nums ${signClass(trade.return_pct)}`}
                          >
                            {formatPercent(trade.return_pct, { sign: true })}
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </div>
              )}
            </CardContent>
          </Card>
        </div>
      ) : null}

      <Card className="mt-6">
        <CardHeader>
          <CardTitle>How this was run</CardTitle>
          <CardDescription>Enough to reproduce the result exactly.</CardDescription>
        </CardHeader>
        <CardContent>
          <dl className="grid gap-x-10 md:grid-cols-2">
            <Detail label="Strategy">
              <Link href={`/strategies/${config.strategy.id}`} className="underline">
                {strategyLabel(config.strategy.type)} v{config.strategy.version}
              </Link>
            </Detail>
            <Detail label="Parameters">
              <ParameterList parameters={config.strategy.parameters} />
            </Detail>
            <Detail label="Instrument">
              {config.instrument.symbol} ({currency})
            </Detail>
            <Detail label="Bars">
              {config.request.interval}
              {results ? ` · ${results.data.bars} from ${results.data.provider}` : ""}
            </Detail>
            <Detail label="Prices">{humanize(config.execution.adjustment_mode)}</Detail>
            <Detail label="Fills">{humanize(config.execution.fill)}</Detail>
            <Detail label="Commission / slippage">
              {config.request.commission_bps} / {config.request.slippage_bps} bps
            </Detail>
            <Detail label="Position size">
              {formatPercent(toNumber(config.request.position_size), { digits: 0 })}
              {config.request.allow_short ? " · shorting allowed" : ""}
            </Detail>
            <Detail label="Engine version">{data.engine_version}</Detail>
            <Detail label="Completed">{formatDateTime(data.completed_at)}</Detail>
            <Detail label="Data fingerprint">
              <span className="font-mono text-xs break-all">{data.data_fingerprint ?? "—"}</span>
            </Detail>
          </dl>
        </CardContent>
      </Card>
    </>
  );
}
