"use client";

import Link from "next/link";
import { CheckCircle2, Circle } from "lucide-react";

import { EquityChart } from "@/components/charts/equity-chart";
import { PageHeader } from "@/components/page-header";
import { Stat, StatGrid } from "@/components/stat";
import { ErrorState, LoadingRows } from "@/components/states";
import { StatusBadge } from "@/components/status-badge";
import { buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  useExperiments,
  usePortfolios,
  useRiskReports,
  useSnapshots,
  useStrategies,
  useValuation,
} from "@/hooks/use-api";
import {
  type BacktestMetrics,
  type Portfolio,
  backtestConfiguration,
  riskResults,
} from "@/lib/api/types";
import {
  formatDate,
  formatMoney,
  formatPercent,
  humanize,
  signClass,
  strategyLabel,
  toNumber,
} from "@/lib/format";

function PortfolioPanel({ portfolio }: { portfolio: Portfolio }) {
  const valuation = useValuation(portfolio.id);
  const snapshots = useSnapshots(portfolio.id);
  const currency = portfolio.base_currency;
  const initial = toNumber(portfolio.initial_capital) ?? 0;
  const equity = toNumber(valuation.data?.total_equity);
  const history = snapshots.data ?? [];

  return (
    <Card>
      <CardHeader>
        <CardTitle>
          <Link href={`/portfolios/${portfolio.id}`} className="hover:underline">
            {portfolio.name}
          </Link>
        </CardTitle>
        <CardDescription>Paper portfolio · {currency}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {valuation.isPending ? (
          <LoadingRows rows={2} />
        ) : valuation.isError ? (
          <ErrorState error={valuation.error} onRetry={() => valuation.refetch()} />
        ) : (
          <StatGrid>
            <Stat
              label="Equity"
              value={formatMoney(valuation.data.total_equity, currency)}
              hint={
                equity !== null && initial > 0
                  ? `${formatPercent(equity / initial - 1, { sign: true })} since opening`
                  : undefined
              }
            />
            <Stat label="Cash" value={formatMoney(valuation.data.cash, currency)} />
            <Stat
              label="Positions"
              value={formatMoney(valuation.data.positions_value, currency)}
              hint={`${valuation.data.positions.length} held`}
            />
            <Stat
              label="Unrealised P&L"
              value={formatMoney(valuation.data.unrealized_pnl, currency, { sign: true })}
              valueClassName={signClass(valuation.data.unrealized_pnl)}
            />
          </StatGrid>
        )}
        {history.length >= 2 ? (
          <EquityChart
            currency={currency}
            baseline={initial}
            height={220}
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
        ) : null}
      </CardContent>
    </Card>
  );
}

function GettingStarted({ steps }: { steps: { done: boolean; label: string; href: string }[] }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Getting started</CardTitle>
        <CardDescription>Four steps from an idea to a measured result.</CardDescription>
      </CardHeader>
      <CardContent>
        <ol className="space-y-3">
          {steps.map(({ done, label, href }) => {
            const Icon = done ? CheckCircle2 : Circle;
            return (
              <li key={href} className="flex items-center gap-3 text-sm">
                <Icon
                  className={`size-5 shrink-0 ${done ? "text-emerald-600" : "text-muted-foreground"}`}
                  aria-hidden
                />
                <span className="sr-only">{done ? "Done:" : "To do:"}</span>
                {done ? (
                  <span className="text-muted-foreground line-through">{label}</span>
                ) : (
                  <Link href={href} className="font-medium underline-offset-4 hover:underline">
                    {label}
                  </Link>
                )}
              </li>
            );
          })}
        </ol>
      </CardContent>
    </Card>
  );
}

export default function DashboardPage() {
  const strategies = useStrategies();
  const experiments = useExperiments();
  const portfolios = usePortfolios();
  const reports = useRiskReports();

  const queries = [strategies, experiments, portfolios, reports];
  const failed = queries.find((query) => query.isError);
  if (failed) return <ErrorState error={failed.error} onRetry={() => failed.refetch()} />;
  if (queries.some((query) => query.isPending)) return <LoadingRows rows={8} />;

  const experimentItems = experiments.data!.items;
  const portfolioItems = portfolios.data!.items;
  const reportItems = reports.data!.items;
  const steps = [
    { done: strategies.data!.total > 0, label: "Create a strategy", href: "/strategies" },
    {
      done: experimentItems.some((experiment) => experiment.status === "COMPLETED"),
      label: "Run a backtest",
      href: "/backtests/new",
    },
    { done: portfolioItems.length > 0, label: "Open a paper portfolio", href: "/portfolios" },
    { done: reportItems.length > 0, label: "Measure risk", href: "/risk" },
  ];
  const onboarding = steps.some((step) => !step.done);
  const latestReport = reportItems[0];

  return (
    <>
      <PageHeader
        title="Dashboard"
        actions={
          <Link href="/backtests/new" className={buttonVariants()}>
            New backtest
          </Link>
        }
      />
      <div className="space-y-6">
        {onboarding ? <GettingStarted steps={steps} /> : null}

        <StatGrid>
          <Stat label="Strategies" value={strategies.data!.total} />
          <Stat label="Backtests" value={experiments.data!.total} />
          <Stat label="Portfolios" value={portfolios.data!.total} />
          <Stat
            label="Latest value at risk"
            value={latestReport ? formatPercent(riskResults(latestReport).value_at_risk) : "—"}
            hint={
              latestReport
                ? `${humanize(latestReport.method)}, ${formatDate(latestReport.created_at)}`
                : "No reports yet"
            }
          />
        </StatGrid>

        {portfolioItems.slice(0, 2).map((portfolio) => (
          <PortfolioPanel key={portfolio.id} portfolio={portfolio} />
        ))}

        <Card>
          <CardHeader>
            <CardTitle>Recent backtests</CardTitle>
            <CardDescription>
              <Link href="/backtests" className="underline-offset-4 hover:underline">
                See all {experiments.data!.total}
              </Link>
            </CardDescription>
          </CardHeader>
          <CardContent>
            {experimentItems.length === 0 ? (
              <p className="text-sm text-muted-foreground">Nothing has been run yet.</p>
            ) : (
              <ul className="divide-y">
                {experimentItems.slice(0, 5).map((experiment) => {
                  const config = backtestConfiguration(experiment);
                  const metrics = experiment.metrics as BacktestMetrics | null | undefined;
                  return (
                    <li key={experiment.id} className="flex items-center gap-3 py-3 text-sm">
                      <Link
                        href={`/backtests/${experiment.id}`}
                        className="min-w-0 flex-1 truncate font-medium hover:underline"
                      >
                        {strategyLabel(config.strategy.type)} v{config.strategy.version} on{" "}
                        {config.instrument.symbol}
                      </Link>
                      <StatusBadge status={experiment.status} />
                      <span
                        className={`w-20 text-right tabular-nums ${signClass(metrics?.total_return)}`}
                      >
                        {formatPercent(metrics?.total_return, { sign: true })}
                      </span>
                    </li>
                  );
                })}
              </ul>
            )}
          </CardContent>
        </Card>
      </div>
    </>
  );
}
