"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { Search } from "lucide-react";

import { PriceChart } from "@/components/charts/price-chart";
import { NativeSelect } from "@/components/field";
import { PageHeader } from "@/components/page-header";
import { Stat, StatGrid } from "@/components/stat";
import { EmptyState, ErrorState, LoadingRows, Warnings } from "@/components/states";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { useCandles, useInstruments } from "@/hooks/use-api";
import type { Instrument } from "@/lib/api/types";
import { formatDate, formatMoney, formatPercent, signClass } from "@/lib/format";
import { cn } from "@/lib/utils";

const RANGES = [
  { label: "6M", months: 6 },
  { label: "1Y", months: 12 },
  { label: "3Y", months: 36 },
  { label: "5Y", months: 60 },
];

function startFor(months: number): string {
  const date = new Date();
  date.setUTCMonth(date.getUTCMonth() - months);
  return `${date.toISOString().slice(0, 10)}T00:00:00Z`;
}

function History({ instrument }: { instrument: Instrument }) {
  const [months, setMonths] = useState(12);
  const [interval, setInterval] = useState("1d");
  const [mode, setMode] = useState<"line" | "candles">("line");
  const start = useMemo(() => startFor(months), [months]);
  const history = useCandles(instrument.id, start, interval);

  const candles = history.data?.candles ?? [];
  const first = candles[0];
  const last = candles[candles.length - 1];
  const change = first && last ? Number(last.close) / Number(first.close) - 1 : null;
  const currency = instrument.currency_code;

  return (
    <Card>
      <CardHeader>
        <CardTitle>
          {instrument.symbol} · {instrument.name}
        </CardTitle>
        <CardDescription>
          {instrument.exchange.name} ({instrument.exchange.mic}) · {currency} ·{" "}
          {instrument.asset_type.toLowerCase()}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex flex-wrap items-center gap-2">
          <div role="group" aria-label="Period" className="flex gap-1">
            {RANGES.map((range) => (
              <Button
                key={range.label}
                size="sm"
                variant={months === range.months ? "default" : "outline"}
                aria-pressed={months === range.months}
                onClick={() => setMonths(range.months)}
              >
                {range.label}
              </Button>
            ))}
          </div>
          <NativeSelect
            aria-label="Bar interval"
            className="w-28"
            value={interval}
            onChange={(event) => setInterval(event.target.value)}
          >
            <option value="1d">Daily</option>
            <option value="1wk">Weekly</option>
            <option value="1mo">Monthly</option>
          </NativeSelect>
          <NativeSelect
            aria-label="Chart type"
            className="w-28"
            value={mode}
            onChange={(event) => setMode(event.target.value as typeof mode)}
          >
            <option value="line">Line</option>
            <option value="candles">Candles</option>
          </NativeSelect>
          <Link
            href={`/backtests/new?instrument=${instrument.id}`}
            className={buttonVariants({ variant: "outline", size: "sm", className: "ml-auto" })}
          >
            Backtest on this
          </Link>
        </div>

        {history.isPending ? (
          <LoadingRows rows={6} />
        ) : history.isError ? (
          <ErrorState error={history.error} onRetry={() => history.refetch()} />
        ) : candles.length === 0 ? (
          <EmptyState title="No prices for this period" />
        ) : (
          <>
            <Warnings items={history.data.warnings} />
            <StatGrid>
              <Stat
                label="Last close"
                value={formatMoney(last.close, currency)}
                hint={formatDate(last.timestamp)}
              />
              <Stat
                label="Change over period"
                value={formatPercent(change, { sign: true })}
                valueClassName={signClass(change)}
              />
              <Stat
                label="High"
                value={formatMoney(Math.max(...candles.map((candle) => Number(candle.high))), currency)}
              />
              <Stat
                label="Low"
                value={formatMoney(Math.min(...candles.map((candle) => Number(candle.low))), currency)}
              />
            </StatGrid>
            <PriceChart candles={candles} currency={currency} mode={mode} />
            <p className="text-xs text-muted-foreground">
              {history.data.count} bars, adjusted for splits and dividends. Research data from{" "}
              Yahoo Finance: delayed and not exchange-grade. Fingerprint{" "}
              <span className="font-mono">{history.data.data_fingerprint.slice(0, 16)}…</span>
            </p>
          </>
        )}
      </CardContent>
    </Card>
  );
}

export default function MarketPage() {
  const [search, setSearch] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const instruments = useInstruments();

  const visible = useMemo(() => {
    const needle = search.trim().toLowerCase();
    const items = instruments.data?.items ?? [];
    if (!needle) return items;
    return items.filter(
      (instrument) =>
        instrument.symbol.toLowerCase().includes(needle) ||
        instrument.name.toLowerCase().includes(needle),
    );
  }, [instruments.data, search]);
  const selected =
    instruments.data?.items.find((instrument) => instrument.id === selectedId) ?? visible[0];

  return (
    <>
      <PageHeader
        title="Market data"
        description="Daily, weekly and monthly prices for the instruments you can backtest and trade."
      />
      {instruments.isPending ? (
        <LoadingRows />
      ) : instruments.isError ? (
        <ErrorState error={instruments.error} onRetry={() => instruments.refetch()} />
      ) : (
        <div className="grid gap-6 lg:grid-cols-[18rem_1fr]">
          <div className="space-y-3">
            <div className="relative">
              <Search
                className="absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground"
                aria-hidden
              />
              <Input
                aria-label="Search instruments"
                placeholder="Search symbol or name"
                className="pl-8"
                value={search}
                onChange={(event) => setSearch(event.target.value)}
              />
            </div>
            {visible.length === 0 ? (
              <p className="p-2 text-sm text-muted-foreground">No instrument matches.</p>
            ) : (
              <ul className="space-y-1">
                {visible.map((instrument) => (
                  <li key={instrument.id}>
                    <button
                      type="button"
                      aria-current={instrument.id === selected?.id ? "true" : undefined}
                      onClick={() => setSelectedId(instrument.id)}
                      className={cn(
                        "flex w-full items-center justify-between gap-2 rounded-lg px-3 py-2 text-left text-sm transition-colors",
                        instrument.id === selected?.id ? "bg-muted font-medium" : "hover:bg-muted/60",
                      )}
                    >
                      <span className="min-w-0">
                        <span className="block">{instrument.symbol}</span>
                        <span className="block truncate text-xs text-muted-foreground">
                          {instrument.name}
                        </span>
                      </span>
                      <span className="shrink-0 text-xs text-muted-foreground">
                        {instrument.exchange.mic}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
          {selected ? <History key={selected.id} instrument={selected} /> : null}
        </div>
      )}
    </>
  );
}
