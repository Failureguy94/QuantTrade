"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useQueries } from "@tanstack/react-query";

import { EquityChart } from "@/components/charts/equity-chart";
import { PageHeader } from "@/components/page-header";
import { EmptyState, ErrorState, LoadingRows } from "@/components/states";
import { buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { keys } from "@/hooks/use-api";
import { api, unwrap } from "@/lib/api/client";
import {
  type BacktestMetrics,
  type Experiment,
  backtestConfiguration,
  backtestResults,
} from "@/lib/api/types";
import { formatDate, formatPercent, formatRatio, strategyLabel } from "@/lib/format";

type Row = {
  label: string;
  value: (metrics: BacktestMetrics) => number | null;
  format: (value: number | null) => string;
  /** Which direction is better, to highlight the best run. */
  better: "higher" | "lower" | "none";
};

const ROWS: Row[] = [
  { label: "Total return", value: (m) => m.total_return, format: (v) => formatPercent(v, { sign: true }), better: "higher" },
  { label: "Buy and hold", value: (m) => m.benchmark_buy_and_hold_return, format: (v) => formatPercent(v, { sign: true }), better: "none" },
  { label: "Annual return", value: (m) => m.cagr, format: (v) => formatPercent(v, { sign: true }), better: "higher" },
  { label: "Annual volatility", value: (m) => m.annualized_volatility, format: (v) => formatPercent(v), better: "lower" },
  { label: "Sharpe ratio", value: (m) => m.sharpe_ratio, format: (v) => formatRatio(v), better: "higher" },
  { label: "Sortino ratio", value: (m) => m.sortino_ratio, format: (v) => formatRatio(v), better: "higher" },
  // Drawdowns are negative, so the best one is the highest (closest to zero).
  { label: "Max drawdown", value: (m) => m.max_drawdown, format: (v) => formatPercent(v), better: "higher" },
  { label: "Closed trades", value: (m) => m.closed_trades, format: (v) => (v === null ? "—" : String(v)), better: "none" },
  { label: "Win rate", value: (m) => m.win_rate, format: (v) => formatPercent(v, { digits: 0 }), better: "higher" },
  { label: "Profit factor", value: (m) => m.profit_factor, format: (v) => formatRatio(v), better: "higher" },
  { label: "Time in market", value: (m) => m.exposure, format: (v) => formatPercent(v, { digits: 0 }), better: "none" },
];

function label(experiment: Experiment, index: number): string {
  const config = backtestConfiguration(experiment);
  return `${index + 1}. ${strategyLabel(config.strategy.type)} v${config.strategy.version} · ${config.instrument.symbol}`;
}

function bestIndex(values: (number | null)[], better: Row["better"]): number | null {
  if (better === "none") return null;
  const present = values.filter((value): value is number => value !== null);
  if (present.length < 2) return null;
  const target = better === "higher" ? Math.max(...present) : Math.min(...present);
  // No highlight when the runs tie.
  return present.filter((value) => value === target).length === 1 ? values.indexOf(target) : null;
}

export default function ComparePage() {
  const ids = (useSearchParams().get("ids") ?? "").split(",").filter(Boolean).slice(0, 4);
  const queries = useQueries({
    queries: ids.map((id) => ({
      queryKey: keys.experiment(id),
      queryFn: () =>
        unwrap(
          api.GET("/api/v1/experiments/{experiment_id}", {
            params: { path: { experiment_id: id } },
          }),
        ),
    })),
  });

  const header = (
    <PageHeader
      back={{ href: "/backtests", label: "Backtests" }}
      title="Compare backtests"
      description="Equity is shown as growth from each run's start, so runs with different capital line up."
    />
  );

  if (ids.length < 2) {
    return (
      <>
        {header}
        <EmptyState
          title="Pick at least two backtests"
          description="Tick the runs you want on the backtests page, then choose Compare."
          action={
            <Link href="/backtests" className={buttonVariants()}>
              Go to backtests
            </Link>
          }
        />
      </>
    );
  }
  if (queries.some((query) => query.isPending)) {
    return (
      <>
        {header}
        <LoadingRows rows={6} />
      </>
    );
  }
  const failed = queries.find((query) => query.isError);
  if (failed) {
    return (
      <>
        {header}
        <ErrorState error={failed.error} />
      </>
    );
  }

  const experiments = queries
    .map((query) => query.data)
    .filter((experiment): experiment is Experiment => Boolean(experiment && backtestResults(experiment)));

  return (
    <>
      {header}
      <div className="space-y-6">
        <Card>
          <CardHeader>
            <CardTitle>Growth</CardTitle>
          </CardHeader>
          <CardContent>
            <EquityChart
              asPercent
              baseline={0}
              series={experiments.map((experiment, index) => ({
                name: label(experiment, index),
                points: backtestResults(experiment)!.equity_curve.map((point) => ({
                  timestamp: point.timestamp,
                  value: Number(point.equity),
                })),
              }))}
            />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Metrics</CardTitle>
            <CardDescription>The best value in each row is in bold.</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="overflow-x-auto rounded-lg border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Metric</TableHead>
                    {experiments.map((experiment, index) => (
                      <TableHead key={experiment.id} className="text-right">
                        <Link href={`/backtests/${experiment.id}`} className="hover:underline">
                          {label(experiment, index)}
                        </Link>
                      </TableHead>
                    ))}
                  </TableRow>
                </TableHeader>
                <TableBody>
                  <TableRow>
                    <TableCell className="text-muted-foreground">Period</TableCell>
                    {experiments.map((experiment) => {
                      const { request } = backtestConfiguration(experiment);
                      return (
                        <TableCell key={experiment.id} className="text-right whitespace-nowrap">
                          {formatDate(request.start)} – {formatDate(request.end)}
                        </TableCell>
                      );
                    })}
                  </TableRow>
                  {ROWS.map((row) => {
                    const values = experiments.map((experiment) =>
                      row.value(backtestResults(experiment)!.metrics),
                    );
                    const best = bestIndex(values, row.better);
                    return (
                      <TableRow key={row.label}>
                        <TableCell className="text-muted-foreground">{row.label}</TableCell>
                        {values.map((value, index) => (
                          <TableCell
                            key={experiments[index].id}
                            className={`text-right tabular-nums ${index === best ? "font-semibold" : ""}`}
                          >
                            {row.format(value)}
                          </TableCell>
                        ))}
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            </div>
          </CardContent>
        </Card>
      </div>
    </>
  );
}
