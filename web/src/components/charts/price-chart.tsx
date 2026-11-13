"use client";

import {
  Bar,
  CartesianGrid,
  ComposedChart,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import type { Candle } from "@/lib/api/types";
import { formatDate, formatMoney, formatNumber } from "@/lib/format";

import { axisProps, gridProps, tooltipProps } from "./chart-theme";

type Row = {
  timestamp: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  range: [number, number];
};

type CandleShapeProps = { x?: number; y?: number; width?: number; height?: number; payload?: Row };

const UP = "var(--chart-2)";
const DOWN = "var(--chart-5)";

/** One candle: the bar spans low to high; the body is drawn inside it. */
function CandleShape({ x = 0, y = 0, width = 0, height = 0, payload }: CandleShapeProps) {
  if (!payload) return null;
  const { open, close, high, low } = payload;
  const span = high - low;
  const scale = span > 0 ? height / span : 0;
  const colour = close >= open ? UP : DOWN;
  const bodyTop = y + (high - Math.max(open, close)) * scale;
  const bodyHeight = Math.max(Math.abs(close - open) * scale, 1);
  const centre = x + width / 2;
  const bodyWidth = Math.max(width * 0.7, 1);
  return (
    <g>
      <line x1={centre} x2={centre} y1={y} y2={y + height} stroke={colour} strokeWidth={1} />
      <rect x={centre - bodyWidth / 2} y={bodyTop} width={bodyWidth} height={bodyHeight} fill={colour} />
    </g>
  );
}

function shortDate(value: string): string {
  return new Intl.DateTimeFormat("en-US", { month: "short", year: "2-digit", timeZone: "UTC" }).format(
    new Date(value),
  );
}

function PriceTooltip({
  active,
  payload,
  currency,
}: {
  active?: boolean;
  payload?: { payload: Row }[];
  currency: string;
}) {
  const row = payload?.[0]?.payload;
  if (!active || !row) return null;
  return (
    <div style={tooltipProps.contentStyle} className="space-y-0.5 p-2">
      <p style={tooltipProps.labelStyle}>{formatDate(row.timestamp)}</p>
      {(["open", "high", "low", "close"] as const).map((key) => (
        <p key={key} className="flex justify-between gap-6">
          <span className="capitalize">{key}</span>
          <span className="tabular-nums">{formatMoney(row[key], currency)}</span>
        </p>
      ))}
      <p className="flex justify-between gap-6">
        <span>Volume</span>
        <span className="tabular-nums">{formatNumber(row.volume, 0)}</span>
      </p>
    </div>
  );
}

export function PriceChart({
  candles,
  currency,
  mode,
  height = 380,
}: {
  candles: Candle[];
  currency: string;
  mode: "line" | "candles";
  height?: number;
}) {
  const data: Row[] = candles.map((candle) => {
    const low = Number(candle.low);
    const high = Number(candle.high);
    return {
      timestamp: candle.timestamp,
      open: Number(candle.open),
      high,
      low,
      close: Number(candle.close),
      volume: candle.volume,
      range: [low, high],
    };
  });
  return (
    <div role="img" aria-label="Price history" style={{ height }}>
      <ResponsiveContainer width="100%" height="100%">
        <ComposedChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 8 }}>
          <CartesianGrid {...gridProps} />
          <XAxis dataKey="timestamp" tickFormatter={shortDate} minTickGap={48} {...axisProps} />
          <YAxis
            domain={["auto", "auto"]}
            width={72}
            tickFormatter={(value: number) => formatMoney(value, currency, { compact: true })}
            {...axisProps}
          />
          <Tooltip content={<PriceTooltip currency={currency} />} />
          {mode === "candles" ? (
            <Bar
              dataKey="range"
              isAnimationActive={false}
              shape={(props: unknown) => <CandleShape {...(props as CandleShapeProps)} />}
            />
          ) : (
            <Line
              type="monotone"
              dataKey="close"
              stroke="var(--chart-1)"
              strokeWidth={2}
              dot={false}
              isAnimationActive={false}
            />
          )}
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}
