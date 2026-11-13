"use client";

import Link from "next/link";
import { useState } from "react";
import { GitCompareArrows, Plus, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { PageHeader } from "@/components/page-header";
import { EmptyState, ErrorState, LoadingRows } from "@/components/states";
import { StatusBadge } from "@/components/status-badge";
import { Button, buttonVariants } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useDeleteExperiment, useExperiments } from "@/hooks/use-api";
import { errorMessage } from "@/lib/api/client";
import { type BacktestMetrics, backtestConfiguration } from "@/lib/api/types";
import { formatDate, formatPercent, formatRatio, signClass, strategyLabel } from "@/lib/format";

const MAX_COMPARED = 4;

export default function BacktestsPage() {
  const experiments = useExperiments();
  const remove = useDeleteExperiment();
  const [selected, setSelected] = useState<string[]>([]);

  function toggle(id: string) {
    setSelected((current) =>
      current.includes(id)
        ? current.filter((value) => value !== id)
        : current.length < MAX_COMPARED
          ? [...current, id]
          : current,
    );
  }

  return (
    <>
      <PageHeader
        title="Backtests"
        description="Every run is stored with its exact configuration and a fingerprint of the data it used."
        actions={
          <>
            {selected.length >= 2 ? (
              <Link
                href={`/backtests/compare?ids=${selected.join(",")}`}
                className={buttonVariants({ variant: "outline" })}
              >
                <GitCompareArrows className="size-4" aria-hidden />
                Compare {selected.length}
              </Link>
            ) : null}
            <Link href="/backtests/new" className={buttonVariants()}>
              <Plus className="size-4" aria-hidden />
              New backtest
            </Link>
          </>
        }
      />

      {experiments.isPending ? (
        <LoadingRows />
      ) : experiments.isError ? (
        <ErrorState error={experiments.error} onRetry={() => experiments.refetch()} />
      ) : experiments.data.items.length === 0 ? (
        <EmptyState
          title="No backtests yet"
          description="Run a strategy against historical prices to see how it would have done."
          action={
            <Link href="/backtests/new" className={buttonVariants()}>
              New backtest
            </Link>
          }
        />
      ) : (
        <>
          <p className="mb-2 text-sm text-muted-foreground">
            Tick two to {MAX_COMPARED} completed runs to compare them.
          </p>
          <div className="rounded-lg border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-8">
                    <span className="sr-only">Compare</span>
                  </TableHead>
                  <TableHead>Strategy</TableHead>
                  <TableHead>Instrument</TableHead>
                  <TableHead>Period</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead className="text-right">Return</TableHead>
                  <TableHead className="text-right">Sharpe</TableHead>
                  <TableHead className="text-right">Max drawdown</TableHead>
                  <TableHead className="text-right">
                    <span className="sr-only">Actions</span>
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {experiments.data.items.map((experiment) => {
                  const config = backtestConfiguration(experiment);
                  const metrics = experiment.metrics as BacktestMetrics | null | undefined;
                  const label = `${strategyLabel(config.strategy.type)} v${config.strategy.version}`;
                  return (
                    <TableRow key={experiment.id}>
                      <TableCell>
                        <input
                          type="checkbox"
                          aria-label={`Compare ${label} on ${config.instrument.symbol}`}
                          disabled={
                            experiment.status !== "COMPLETED" ||
                            (!selected.includes(experiment.id) && selected.length >= MAX_COMPARED)
                          }
                          checked={selected.includes(experiment.id)}
                          onChange={() => toggle(experiment.id)}
                        />
                      </TableCell>
                      <TableCell className="font-medium">
                        <Link href={`/backtests/${experiment.id}`} className="hover:underline">
                          {label}
                        </Link>
                      </TableCell>
                      <TableCell>{config.instrument.symbol}</TableCell>
                      <TableCell className="whitespace-nowrap">
                        {formatDate(config.request.start)} – {formatDate(config.request.end)}
                      </TableCell>
                      <TableCell>
                        <StatusBadge status={experiment.status} />
                      </TableCell>
                      <TableCell
                        className={`text-right tabular-nums ${signClass(metrics?.total_return)}`}
                      >
                        {formatPercent(metrics?.total_return, { sign: true })}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {formatRatio(metrics?.sharpe_ratio)}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {formatPercent(metrics?.max_drawdown)}
                      </TableCell>
                      <TableCell className="text-right">
                        <Button
                          variant="ghost"
                          size="icon-sm"
                          aria-label={`Delete ${label} on ${config.instrument.symbol}`}
                          disabled={remove.isPending}
                          onClick={() => {
                            if (!window.confirm("Delete this backtest? This cannot be undone.")) return;
                            remove.mutate(experiment.id, {
                              onSuccess: () => {
                                setSelected((current) => current.filter((id) => id !== experiment.id));
                                toast.success("Backtest deleted");
                              },
                              onError: (error) => toast.error(errorMessage(error)),
                            });
                          }}
                        >
                          <Trash2 className="size-4" aria-hidden />
                        </Button>
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </div>
        </>
      )}
    </>
  );
}
