"use client";

import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { formatPercent } from "@/lib/format";

import { axisProps, gridProps, tooltipProps } from "./chart-theme";

export type RiskBar = { method: string; valueAtRisk: number; expectedShortfall: number };

export function RiskChart({ data, height = 260 }: { data: RiskBar[]; height?: number }) {
  return (
    <div role="img" aria-label="Value at risk and expected shortfall by method" style={{ height }}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 8 }}>
          <CartesianGrid {...gridProps} />
          <XAxis dataKey="method" {...axisProps} />
          <YAxis
            width={64}
            tickFormatter={(value: number) => formatPercent(value, { digits: 1 })}
            {...axisProps}
          />
          <Tooltip
            {...tooltipProps}
            cursor={{ fill: "var(--muted)", opacity: 0.4 }}
            formatter={(value) => formatPercent(Number(value))}
          />
          <Legend wrapperStyle={{ fontSize: 12 }} />
          <Bar
            dataKey="valueAtRisk"
            name="Value at risk"
            fill="var(--chart-1)"
            radius={[4, 4, 0, 0]}
            isAnimationActive={false}
          />
          <Bar
            dataKey="expectedShortfall"
            name="Expected shortfall"
            fill="var(--chart-5)"
            radius={[4, 4, 0, 0]}
            isAnimationActive={false}
          />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
