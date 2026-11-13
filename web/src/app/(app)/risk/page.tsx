"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";

import { RiskChart } from "@/components/charts/risk-chart";
import { Field, FormError, NativeSelect } from "@/components/field";
import { PageHeader } from "@/components/page-header";
import { Stat, StatGrid } from "@/components/stat";
import { EmptyState, ErrorState, LoadingRows, Spinner, Warnings } from "@/components/states";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  useCreateRiskReport,
  useExperiments,
  usePortfolios,
  useRiskReports,
} from "@/hooks/use-api";
import { errorMessage } from "@/lib/api/client";
import { type RiskReport, backtestConfiguration, riskResults } from "@/lib/api/types";
import { formatDateTime, formatMoney, formatPercent, humanize, strategyLabel } from "@/lib/format";

type Method = "HISTORICAL" | "PARAMETRIC" | "MONTE_CARLO";

const METHODS: { value: Method; label: string; hint: string }[] = [
  {
    value: "HISTORICAL",
    label: "Historical",
    hint: "Uses the actual past returns as the distribution. No assumption about its shape.",
  },
  {
    value: "PARAMETRIC",
    label: "Parametric",
    hint: "Assumes returns are normally distributed. Understates risk when they have fat tails.",
  },
  {
    value: "MONTE_CARLO",
    label: "Monte Carlo",
    hint: "Simulates many paths from the estimated mean and covariance of returns.",
  },
];

function ReportSummary({ report, title }: { report: RiskReport; title: string }) {
  const results = riskResults(report);
  const currency = results.currency ?? "USD";
  const confidence = formatPercent(results.confidence_level, { digits: 0 });
  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
        <CardDescription>
          {humanize(results.method)} · {confidence} confidence · {results.horizon_periods}-period
          horizon · {results.observations} observations
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <Warnings items={results.warnings} />
        <StatGrid>
          <Stat
            label="Value at risk"
            value={formatPercent(results.value_at_risk)}
            hint={formatMoney(results.value_at_risk_amount, currency)}
          />
          <Stat
            label="Expected shortfall"
            value={formatPercent(results.expected_shortfall)}
            hint={formatMoney(results.expected_shortfall_amount, currency)}
          />
          <Stat
            label="Annual volatility"
            value={formatPercent(results.annualized_volatility)}
          />
          <Stat label="Worst period" value={formatPercent(results.worst_period_return)} />
        </StatGrid>
        <p className="text-sm text-muted-foreground">
          With {confidence} confidence the loss over the horizon should not exceed the value at
          risk. In the remaining cases, the average loss is the expected shortfall. Both are
          estimates from past data, not limits.
        </p>
      </CardContent>
    </Card>
  );
}

export default function RiskPage() {
  const params = useSearchParams();
  const portfolios = usePortfolios();
  const experiments = useExperiments();
  const reports = useRiskReports();
  const create = useCreateRiskReport();

  const presetExperiment = params.get("experiment") ?? "";
  const [subject, setSubject] = useState<"portfolio" | "experiment">(
    presetExperiment ? "experiment" : "portfolio",
  );
  const [portfolioId, setPortfolioId] = useState(params.get("portfolio") ?? "");
  const [experimentId, setExperimentId] = useState(presetExperiment);
  const [method, setMethod] = useState<Method>("HISTORICAL");
  const [confidence, setConfidence] = useState("0.95");
  const [horizon, setHorizon] = useState("1");
  const [lookback, setLookback] = useState("252");
  const [latest, setLatest] = useState<RiskReport | null>(null);

  const portfolioItems = portfolios.data?.items ?? [];
  const completed = (experiments.data?.items ?? []).filter(
    (experiment) => experiment.status === "COMPLETED",
  );
  const selectedPortfolio = portfolioId || portfolioItems[0]?.id || "";
  const selectedExperiment = experimentId || completed[0]?.id || "";
  const subjectId = subject === "portfolio" ? selectedPortfolio : selectedExperiment;

  const experimentLabel = (id: string | null) => {
    const experiment = experiments.data?.items.find((candidate) => candidate.id === id);
    if (!experiment) return "Deleted backtest";
    const config = backtestConfiguration(experiment);
    return `${strategyLabel(config.strategy.type)} v${config.strategy.version} on ${config.instrument.symbol}`;
  };
  const portfolioLabel = (id: string | null) =>
    portfolioItems.find((candidate) => candidate.id === id)?.name ?? "Deleted portfolio";
  const subjectLabel = (report: RiskReport) =>
    report.portfolio_id
      ? portfolioLabel(report.portfolio_id)
      : experimentLabel(report.experiment_id);

  // The newest report per method for the selected subject, to compare methods.
  const comparison = METHODS.flatMap(({ value, label }) => {
    const match = (reports.data?.items ?? []).find(
      (report) =>
        report.method === value &&
        (subject === "portfolio"
          ? report.portfolio_id === subjectId
          : report.experiment_id === subjectId),
    );
    if (!match) return [];
    const results = riskResults(match);
    return [
      { method: label, valueAtRisk: results.value_at_risk, expectedShortfall: results.expected_shortfall },
    ];
  });

  function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!subjectId) return;
    create.mutate(
      {
        portfolio_id: subject === "portfolio" ? subjectId : null,
        experiment_id: subject === "experiment" ? subjectId : null,
        method,
        confidence_level: Number(confidence),
        horizon_days: Number(horizon),
        lookback_days: Number(lookback),
      },
      {
        onSuccess: (report) => {
          setLatest(report);
          toast.success("Risk report ready");
        },
      },
    );
  }

  const nothingToMeasure =
    !portfolios.isPending && !experiments.isPending && !portfolioItems.length && !completed.length;

  return (
    <>
      <PageHeader
        title="Risk"
        description="Value at risk and expected shortfall for a portfolio's current holdings or a backtest's returns."
      />
      {nothingToMeasure ? (
        <EmptyState
          title="Nothing to measure yet"
          description="Open a portfolio and buy something, or complete a backtest, then come back."
          action={
            <span className="flex gap-3 text-sm">
              <Link href="/portfolios" className="underline">
                Portfolios
              </Link>
              <Link href="/backtests" className="underline">
                Backtests
              </Link>
            </span>
          }
        />
      ) : (
        <div className="grid gap-6 lg:grid-cols-[22rem_1fr]">
          <Card className="h-fit">
            <CardHeader>
              <CardTitle>New report</CardTitle>
            </CardHeader>
            <CardContent>
              <form onSubmit={submit} className="space-y-4">
                <Field label="Measure">
                  {(props) => (
                    <NativeSelect
                      {...props}
                      value={subject}
                      onChange={(event) => setSubject(event.target.value as typeof subject)}
                    >
                      <option value="portfolio">A portfolio</option>
                      <option value="experiment">A backtest</option>
                    </NativeSelect>
                  )}
                </Field>
                {subject === "portfolio" ? (
                  <Field
                    label="Portfolio"
                    error={portfolioItems.length ? null : "You have no portfolios yet."}
                  >
                    {(props) => (
                      <NativeSelect
                        {...props}
                        value={selectedPortfolio}
                        onChange={(event) => setPortfolioId(event.target.value)}
                      >
                        {portfolioItems.map((portfolio) => (
                          <option key={portfolio.id} value={portfolio.id}>
                            {portfolio.name}
                          </option>
                        ))}
                      </NativeSelect>
                    )}
                  </Field>
                ) : (
                  <Field
                    label="Backtest"
                    error={completed.length ? null : "You have no completed backtests yet."}
                  >
                    {(props) => (
                      <NativeSelect
                        {...props}
                        value={selectedExperiment}
                        onChange={(event) => setExperimentId(event.target.value)}
                      >
                        {completed.map((experiment) => (
                          <option key={experiment.id} value={experiment.id}>
                            {experimentLabel(experiment.id)}
                          </option>
                        ))}
                      </NativeSelect>
                    )}
                  </Field>
                )}
                <Field
                  label="Method"
                  hint={METHODS.find((candidate) => candidate.value === method)?.hint}
                >
                  {(props) => (
                    <NativeSelect
                      {...props}
                      value={method}
                      onChange={(event) => setMethod(event.target.value as Method)}
                    >
                      {METHODS.map((candidate) => (
                        <option key={candidate.value} value={candidate.value}>
                          {candidate.label}
                        </option>
                      ))}
                    </NativeSelect>
                  )}
                </Field>
                <div className="grid grid-cols-2 gap-4">
                  <Field label="Confidence">
                    {(props) => (
                      <NativeSelect
                        {...props}
                        value={confidence}
                        onChange={(event) => setConfidence(event.target.value)}
                      >
                        <option value="0.9">90%</option>
                        <option value="0.95">95%</option>
                        <option value="0.99">99%</option>
                      </NativeSelect>
                    )}
                  </Field>
                  <Field label="Horizon (days)">
                    {(props) => (
                      <Input
                        {...props}
                        type="number"
                        min="1"
                        max="250"
                        step="1"
                        required
                        value={horizon}
                        onChange={(event) => setHorizon(event.target.value)}
                      />
                    )}
                  </Field>
                </div>
                {subject === "portfolio" ? (
                  <Field label="History to use (trading days)" hint="252 is about one year.">
                    {(props) => (
                      <Input
                        {...props}
                        type="number"
                        min="30"
                        max="2520"
                        step="1"
                        required
                        value={lookback}
                        onChange={(event) => setLookback(event.target.value)}
                      />
                    )}
                  </Field>
                ) : null}
                <FormError message={create.error ? errorMessage(create.error) : null} />
                <Button type="submit" disabled={create.isPending || !subjectId}>
                  {create.isPending ? <Spinner /> : null}
                  Calculate
                </Button>
              </form>
            </CardContent>
          </Card>

          <div className="space-y-6">
            {latest ? <ReportSummary report={latest} title={subjectLabel(latest)} /> : null}

            {comparison.length >= 2 ? (
              <Card>
                <CardHeader>
                  <CardTitle>Methods compared</CardTitle>
                  <CardDescription>
                    The newest report of each method for the selected{" "}
                    {subject === "portfolio" ? "portfolio" : "backtest"}. Settings may differ
                    between them.
                  </CardDescription>
                </CardHeader>
                <CardContent>
                  <RiskChart data={comparison} />
                </CardContent>
              </Card>
            ) : null}

            <Card>
              <CardHeader>
                <CardTitle>History</CardTitle>
              </CardHeader>
              <CardContent>
                {reports.isPending ? (
                  <LoadingRows />
                ) : reports.isError ? (
                  <ErrorState error={reports.error} onRetry={() => reports.refetch()} />
                ) : reports.data.items.length === 0 ? (
                  <EmptyState title="No reports yet" description="Calculate one with the form." />
                ) : (
                  <div className="overflow-x-auto rounded-lg border">
                    <Table>
                      <TableHeader>
                        <TableRow>
                          <TableHead>Created</TableHead>
                          <TableHead>Subject</TableHead>
                          <TableHead>Method</TableHead>
                          <TableHead className="text-right">Confidence</TableHead>
                          <TableHead className="text-right">Value at risk</TableHead>
                          <TableHead className="text-right">Expected shortfall</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {reports.data.items.map((report) => {
                          const results = riskResults(report);
                          return (
                            <TableRow key={report.id}>
                              <TableCell className="whitespace-nowrap">
                                {formatDateTime(report.created_at)}
                              </TableCell>
                              <TableCell>
                                <button
                                  type="button"
                                  className="text-left font-medium hover:underline"
                                  onClick={() => setLatest(report)}
                                >
                                  {subjectLabel(report)}
                                </button>
                                <span className="block text-xs text-muted-foreground">
                                  {report.portfolio_id ? "Portfolio" : "Backtest"}
                                </span>
                              </TableCell>
                              <TableCell>{humanize(report.method)}</TableCell>
                              <TableCell className="text-right tabular-nums">
                                {formatPercent(results.confidence_level, { digits: 0 })}
                              </TableCell>
                              <TableCell className="text-right tabular-nums">
                                {formatPercent(results.value_at_risk)}
                              </TableCell>
                              <TableCell className="text-right tabular-nums">
                                {formatPercent(results.expected_shortfall)}
                              </TableCell>
                            </TableRow>
                          );
                        })}
                      </TableBody>
                    </Table>
                  </div>
                )}
              </CardContent>
            </Card>
          </div>
        </div>
      )}
    </>
  );
}
