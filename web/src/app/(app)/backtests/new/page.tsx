"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";

import { Field, FormError, NativeSelect } from "@/components/field";
import { PageHeader } from "@/components/page-header";
import { ParameterList } from "@/components/parameter-form";
import { EmptyState, ErrorState, LoadingRows, Spinner } from "@/components/states";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  useInstruments,
  useRunBacktest,
  useStrategies,
  useStrategyVersions,
} from "@/hooks/use-api";
import { errorMessage } from "@/lib/api/client";
import { strategyLabel } from "@/lib/format";

function isoDate(date: Date): string {
  return date.toISOString().slice(0, 10);
}

function yearsAgo(years: number): string {
  const date = new Date();
  date.setUTCFullYear(date.getUTCFullYear() - years);
  return isoDate(date);
}

function VersionSelect({
  strategyId,
  value,
  onChange,
}: {
  strategyId: string;
  value: string;
  onChange: (id: string) => void;
}) {
  const versions = useStrategyVersions(strategyId);
  const sorted = [...(versions.data ?? [])].sort((a, b) => b.version - a.version);
  const selected = sorted.find((version) => version.id === value) ?? sorted[0];
  return (
    <Field label="Version">
      {(props) => (
        <>
          <NativeSelect
            {...props}
            value={selected?.id ?? ""}
            disabled={versions.isPending}
            onChange={(event) => onChange(event.target.value)}
          >
            {sorted.map((version) => (
              <option key={version.id} value={version.id}>
                Version {version.version}
              </option>
            ))}
          </NativeSelect>
          {selected ? (
            <p className="text-xs text-muted-foreground">
              <ParameterList parameters={selected.parameters} />
            </p>
          ) : null}
        </>
      )}
    </Field>
  );
}

export default function NewBacktestPage() {
  const router = useRouter();
  const params = useSearchParams();
  const strategies = useStrategies();
  const instruments = useInstruments();
  const run = useRunBacktest();

  const [strategyId, setStrategyId] = useState(params.get("strategy") ?? "");
  const [versionId, setVersionId] = useState(params.get("version") ?? "");
  const [instrumentId, setInstrumentId] = useState(params.get("instrument") ?? "");
  const [start, setStart] = useState(() => yearsAgo(3));
  const [end, setEnd] = useState(() => isoDate(new Date()));
  const [interval, setInterval] = useState<"1d" | "1wk" | "1mo">("1d");
  const [capital, setCapital] = useState("100000");
  const [commission, setCommission] = useState("5");
  const [slippage, setSlippage] = useState("2");
  const [positionSize, setPositionSize] = useState("100");
  const [allowShort, setAllowShort] = useState(false);
  const [fractional, setFractional] = useState(false);

  if (strategies.isPending || instruments.isPending) return <LoadingRows rows={6} />;
  if (strategies.isError) {
    return <ErrorState error={strategies.error} onRetry={() => strategies.refetch()} />;
  }
  if (instruments.isError) {
    return <ErrorState error={instruments.error} onRetry={() => instruments.refetch()} />;
  }
  if (strategies.data.items.length === 0) {
    return (
      <>
        <PageHeader back={{ href: "/backtests", label: "Backtests" }} title="New backtest" />
        <EmptyState
          title="Create a strategy first"
          description="A backtest runs one version of a strategy on one instrument."
          action={
            <Link href="/strategies" className={buttonVariants()}>
              Go to strategies
            </Link>
          }
        />
      </>
    );
  }

  const strategy =
    strategies.data.items.find((candidate) => candidate.id === strategyId) ??
    strategies.data.items[0];
  const instrument =
    instruments.data.items.find((candidate) => candidate.id === instrumentId) ??
    instruments.data.items[0];
  const resolvedVersion = versionId || strategy.latest_version?.id || "";
  const datesValid = start < end;

  function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!instrument || !resolvedVersion || !datesValid) return;
    run.mutate(
      {
        strategy_version_id: resolvedVersion,
        instrument_id: instrument.id,
        start,
        end,
        interval,
        initial_capital: capital,
        commission_bps: commission,
        slippage_bps: slippage,
        position_size: String(Number(positionSize) / 100),
        allow_short: allowShort,
        fractional_shares: fractional,
      },
      { onSuccess: (experiment) => router.push(`/backtests/${experiment.id}`) },
    );
  }

  return (
    <>
      <PageHeader
        back={{ href: "/backtests", label: "Backtests" }}
        title="New backtest"
        description="Signals use prices up to each bar and fill at the next bar's open."
      />
      <form onSubmit={submit} className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>What to test</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <Field label="Strategy">
              {(props) => (
                <NativeSelect
                  {...props}
                  value={strategy.id}
                  onChange={(event) => {
                    setStrategyId(event.target.value);
                    setVersionId("");
                  }}
                >
                  {strategies.data.items.map((candidate) => (
                    <option key={candidate.id} value={candidate.id}>
                      {candidate.name} ({strategyLabel(candidate.strategy_type)})
                    </option>
                  ))}
                </NativeSelect>
              )}
            </Field>
            <VersionSelect
              key={strategy.id}
              strategyId={strategy.id}
              value={resolvedVersion}
              onChange={setVersionId}
            />
            <Field label="Instrument">
              {(props) => (
                <NativeSelect
                  {...props}
                  value={instrument?.id ?? ""}
                  onChange={(event) => setInstrumentId(event.target.value)}
                >
                  {instruments.data.items.map((candidate) => (
                    <option key={candidate.id} value={candidate.id}>
                      {candidate.symbol} · {candidate.name} ({candidate.exchange.mic},{" "}
                      {candidate.currency_code})
                    </option>
                  ))}
                </NativeSelect>
              )}
            </Field>
            <div className="grid grid-cols-2 gap-4">
              <Field label="From">
                {(props) => (
                  <Input
                    {...props}
                    type="date"
                    required
                    value={start}
                    onChange={(event) => setStart(event.target.value)}
                  />
                )}
              </Field>
              <Field label="To" error={datesValid ? null : "Must be after the start date."}>
                {(props) => (
                  <Input
                    {...props}
                    type="date"
                    required
                    value={end}
                    onChange={(event) => setEnd(event.target.value)}
                  />
                )}
              </Field>
            </div>
            <Field label="Bars">
              {(props) => (
                <NativeSelect
                  {...props}
                  value={interval}
                  onChange={(event) => setInterval(event.target.value as typeof interval)}
                >
                  <option value="1d">Daily</option>
                  <option value="1wk">Weekly</option>
                  <option value="1mo">Monthly</option>
                </NativeSelect>
              )}
            </Field>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Execution</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <Field label={`Starting capital (${instrument?.currency_code ?? ""})`}>
              {(props) => (
                <Input
                  {...props}
                  type="number"
                  min="1"
                  step="any"
                  required
                  value={capital}
                  onChange={(event) => setCapital(event.target.value)}
                />
              )}
            </Field>
            <div className="grid grid-cols-2 gap-4">
              <Field label="Commission (bps)" hint="1 bp = 0.01% of the traded value.">
                {(props) => (
                  <Input
                    {...props}
                    type="number"
                    min="0"
                    max="1000"
                    step="any"
                    required
                    value={commission}
                    onChange={(event) => setCommission(event.target.value)}
                  />
                )}
              </Field>
              <Field label="Slippage (bps)" hint="How much worse than the open each fill is.">
                {(props) => (
                  <Input
                    {...props}
                    type="number"
                    min="0"
                    max="1000"
                    step="any"
                    required
                    value={slippage}
                    onChange={(event) => setSlippage(event.target.value)}
                  />
                )}
              </Field>
            </div>
            <Field label="Position size (% of equity)">
              {(props) => (
                <Input
                  {...props}
                  type="number"
                  min="1"
                  max="100"
                  step="any"
                  required
                  value={positionSize}
                  onChange={(event) => setPositionSize(event.target.value)}
                />
              )}
            </Field>
            <label className="flex items-start gap-2 text-sm">
              <input
                type="checkbox"
                className="mt-1"
                checked={allowShort}
                onChange={(event) => setAllowShort(event.target.checked)}
              />
              <span>
                Allow short selling
                <span className="block text-xs text-muted-foreground">
                  Sell signals open a short position instead of going to cash. No borrow cost is
                  charged.
                </span>
              </span>
            </label>
            <label className="flex items-start gap-2 text-sm">
              <input
                type="checkbox"
                className="mt-1"
                checked={fractional}
                onChange={(event) => setFractional(event.target.checked)}
              />
              <span>
                Fractional shares
                <span className="block text-xs text-muted-foreground">
                  Otherwise quantities are rounded down to whole shares.
                </span>
              </span>
            </label>
          </CardContent>
        </Card>

        <div className="space-y-4 lg:col-span-2">
          <FormError message={run.error ? errorMessage(run.error) : null} />
          <Button type="submit" disabled={run.isPending || !datesValid || !resolvedVersion}>
            {run.isPending ? <Spinner /> : null}
            Run backtest
          </Button>
        </div>
      </form>
    </>
  );
}
