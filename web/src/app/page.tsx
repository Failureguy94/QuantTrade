import Link from "next/link";
import { FlaskConical, LineChart, ShieldAlert, Wallet } from "lucide-react";

import { ThemeToggle } from "@/components/app-shell";
import { buttonVariants } from "@/components/ui/button";

const FEATURES = [
  {
    icon: LineChart,
    title: "Strategies",
    body: "Moving-average crossover, momentum and mean reversion, with versioned parameters so every result points at exactly what produced it.",
  },
  {
    icon: FlaskConical,
    title: "Backtests",
    body: "Signals use only data available at the time and fill at the next bar's open, with commission and slippage. Compare runs side by side.",
  },
  {
    icon: Wallet,
    title: "Paper trading",
    body: "Market and limit orders against the latest prices, with positions, cash and a double-entry ledger that always reconciles.",
  },
  {
    icon: ShieldAlert,
    title: "Risk",
    body: "Historical, parametric and Monte Carlo Value-at-Risk and expected shortfall for a portfolio or a backtest.",
  },
];

export default function LandingPage() {
  return (
    <div className="flex min-h-screen flex-col">
      <header className="flex items-center justify-between p-4 md:px-8">
        <span className="flex items-center gap-2 font-semibold tracking-tight">
          <span className="grid size-7 place-items-center rounded-md bg-primary text-primary-foreground">
            <LineChart className="size-4" aria-hidden />
          </span>
          QuantTrade
        </span>
        <nav className="flex items-center gap-2" aria-label="Account">
          <ThemeToggle />
          <Link href="/login" className={buttonVariants({ variant: "ghost" })}>
            Sign in
          </Link>
          <Link href="/register" className={buttonVariants()}>
            Get started
          </Link>
        </nav>
      </header>

      <main className="mx-auto flex w-full max-w-5xl flex-1 flex-col justify-center gap-12 px-4 py-16 md:px-8">
        <section className="space-y-6 text-center">
          <h1 className="text-4xl font-semibold tracking-tight text-balance md:text-6xl">
            Test a trading idea before it costs you anything
          </h1>
          <p className="mx-auto max-w-2xl text-lg text-pretty text-muted-foreground">
            Backtest strategies on historical prices, trade them with simulated money and
            measure the risk you would be taking.
          </p>
          <div className="flex justify-center gap-3">
            <Link href="/register" className={buttonVariants({ size: "lg" })}>
              Create a free account
            </Link>
            <Link href="/login" className={buttonVariants({ size: "lg", variant: "outline" })}>
              Sign in
            </Link>
          </div>
        </section>

        <section aria-label="Features" className="grid gap-4 sm:grid-cols-2">
          {FEATURES.map(({ icon: Icon, title, body }) => (
            <div key={title} className="rounded-xl border bg-card p-6">
              <Icon className="mb-3 size-5 text-muted-foreground" aria-hidden />
              <h2 className="mb-1 font-semibold">{title}</h2>
              <p className="text-sm text-muted-foreground">{body}</p>
            </div>
          ))}
        </section>
      </main>

      <footer className="border-t p-6 text-center text-xs text-muted-foreground">
        Paper trading with simulated money on delayed research data. Nothing here is investment
        advice or a real brokerage service.
      </footer>
    </div>
  );
}
