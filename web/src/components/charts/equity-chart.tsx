"use client";

import {
  Area,
  AreaChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { drawdowns, formatDate, formatMoney, formatPercent } from "@/lib/format";

import { CHART_COLORS, axisProps, gridProps, tooltipProps } from "./chart-theme";

export type Series = { name: string; points: { timestamp: string; value: number }[] };

/** Merge several series on their timestamps so they share one x axis. */
function merge(series: Series[]): Record<string, number | string>[] {
  const rows = new Map<string, Record<string, number | string>>();
  for (const { name, points } of series) {
    for (const { timestamp, value } of points) {
      const row = rows.get(timestamp) ?? { timestamp };
      row[name] = value;
      rows.set(timestamp, row);
    }
  }
  return [...rows.values()].sort((a, b) => String(a.timestamp).localeCompare(String(b.timestamp)));
}

function shortDate(value: string): string {
  return new Intl.DateTimeFormat("en-US", { month: "short", year: "2-digit", timeZone: "UTC" }).format(
    new Date(value),
  );
}

export function EquityChart({
  series,
  currency = "USD",
  baseline,
  height = 320,
  asPercent = false,
}: {
  series: Series[];
  currency?: string;
  baseline?: number;
  height?: number;
  /** Plot each series as growth from its own first value, to compare runs. */
  asPercent?: boolean;
}) {
  const prepared = asPercent
    ? series.map(({ name, points }) => ({
        name,
        points: points.map((point) => ({
          ...point,
          value: points[0]?.value ? point.value / points[0].value - 1 : 0,
        })),
      }))
    : series;
  const data = merge(prepared);
  const format = (value: number) =>
    asPercent ? formatPercent(value, { digits: 1 }) : formatMoney(value, currency, { compact: true });

  return (
    <div role="img" aria-label="Equity over time" style={{ height }}>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 8 }}>
          <CartesianGrid {...gridProps} />
          <XAxis dataKey="timestamp" tickFormatter={shortDate} minTickGap={48} {...axisProps} />
          <YAxis tickFormatter={format} width={72} domain={["auto", "auto"]} {...axisProps} />
          <Tooltip
            {...tooltipProps}
            labelFormatter={(label) => formatDate(String(label))}
            formatter={(value) =>
              asPercent
                ? formatPercent(Number(value), { sign: true })
                : formatMoney(Number(value), currency)
            }
          />
          {series.length > 1 ? <Legend wrapperStyle={{ fontSize: 12 }} /> : null}
          {baseline !== undefined ? (
            <ReferenceLine y={baseline} stroke="var(--muted-foreground)" strokeDasharray="4 4" />
          ) : null}
          {prepared.map(({ name }, index) => (
            <Line
              key={name}
              type="monotone"
              dataKey={name}
              stroke={CHART_COLORS[index % CHART_COLORS.length]}
              strokeWidth={2}
              dot={false}
              isAnimationActive={false}
              connectNulls
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

export function DrawdownChart({
  points,
  height = 180,
}: {
  points: { timestamp: string; value: number }[];
  height?: number;
}) {
  const depth = drawdowns(points.map((point) => point.value));
  const data = points.map((point, index) => ({ timestamp: point.timestamp, drawdown: depth[index] }));
  return (
    <div role="img" aria-label="Drawdown over time" style={{ height }}>
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 8 }}>
          <CartesianGrid {...gridProps} />
          <XAxis dataKey="timestamp" tickFormatter={shortDate} minTickGap={48} {...axisProps} />
          <YAxis
            tickFormatter={(value: number) => formatPercent(value, { digits: 0 })}
            width={72}
            {...axisProps}
          />
          <Tooltip
            {...tooltipProps}
            labelFormatter={(label) => formatDate(String(label))}
            formatter={(value) => formatPercent(Number(value))}
          />
          <Area
            type="monotone"
            dataKey="drawdown"
            name="Drawdown"
            stroke="var(--chart-5)"
            fill="var(--chart-5)"
            fillOpacity={0.2}
            isAnimationActive={false}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
