"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useTheme } from "next-themes";
import { Suspense, useState } from "react";
import {
  CandlestickChart,
  FlaskConical,
  LayoutDashboard,
  LineChart,
  LogOut,
  Menu,
  Moon,
  Settings,
  ShieldAlert,
  Sun,
  Wallet,
  X,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { useLogout, useMe } from "@/hooks/use-api";
import { cn } from "@/lib/utils";

const NAVIGATION = [
  { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
  { href: "/market", label: "Market data", icon: CandlestickChart },
  { href: "/strategies", label: "Strategies", icon: LineChart },
  { href: "/backtests", label: "Backtests", icon: FlaskConical },
  { href: "/portfolios", label: "Portfolios", icon: Wallet },
  { href: "/risk", label: "Risk", icon: ShieldAlert },
  { href: "/settings", label: "Settings", icon: Settings },
];

function NavList({ pathname, onNavigate }: { pathname: string; onNavigate?: () => void }) {
  return (
    <nav aria-label="Main" className="flex flex-col gap-1">
      {NAVIGATION.map(({ href, label, icon: Icon }) => {
        const active = pathname === href || pathname.startsWith(`${href}/`);
        return (
          <Link
            key={href}
            href={href}
            onClick={onNavigate}
            aria-current={active ? "page" : undefined}
            className={cn(
              "flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors",
              active
                ? "bg-sidebar-accent text-sidebar-accent-foreground"
                : "text-muted-foreground hover:bg-sidebar-accent/60 hover:text-foreground",
            )}
          >
            <Icon className="size-4" aria-hidden />
            {label}
          </Link>
        );
      })}
    </nav>
  );
}

function CurrentNavList({ onNavigate }: { onNavigate?: () => void }) {
  return <NavList pathname={usePathname()} onNavigate={onNavigate} />;
}

/**
 * The current path is only known per request, so the highlighted item streams in;
 * until then the links render without one.
 */
function NavLinks({ onNavigate }: { onNavigate?: () => void }) {
  return (
    <Suspense fallback={<NavList pathname="" onNavigate={onNavigate} />}>
      <CurrentNavList onNavigate={onNavigate} />
    </Suspense>
  );
}

export function ThemeToggle() {
  const { resolvedTheme, setTheme } = useTheme();
  return (
    <Button
      variant="ghost"
      size="icon"
      aria-label="Switch between light and dark theme"
      onClick={() => setTheme(resolvedTheme === "dark" ? "light" : "dark")}
    >
      <Sun className="size-4 dark:hidden" aria-hidden />
      <Moon className="hidden size-4 dark:block" aria-hidden />
    </Button>
  );
}

export function Brand() {
  return (
    <Link href="/dashboard" className="flex items-center gap-2 font-semibold tracking-tight">
      <span className="grid size-7 place-items-center rounded-md bg-primary text-primary-foreground">
        <LineChart className="size-4" aria-hidden />
      </span>
      QuantTrade
    </Link>
  );
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const [menuOpen, setMenuOpen] = useState(false);
  const me = useMe();
  const logout = useLogout();

  return (
    <div className="flex min-h-screen w-full">
      <aside className="hidden w-60 shrink-0 flex-col border-r bg-sidebar p-4 md:flex">
        <div className="mb-6 px-1">
          <Brand />
        </div>
        <NavLinks />
        <p className="mt-auto px-1 pt-6 text-xs text-muted-foreground">
          Paper trading only. Not investment advice.
        </p>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-20 flex h-14 items-center gap-3 border-b bg-background/90 px-4 backdrop-blur">
          <Button
            variant="ghost"
            size="icon"
            className="md:hidden"
            aria-label={menuOpen ? "Close menu" : "Open menu"}
            aria-expanded={menuOpen}
            onClick={() => setMenuOpen((open) => !open)}
          >
            {menuOpen ? <X className="size-4" aria-hidden /> : <Menu className="size-4" aria-hidden />}
          </Button>
          <div className="md:hidden">
            <Brand />
          </div>
          <div className="ml-auto flex items-center gap-1">
            <span className="hidden max-w-56 truncate text-sm text-muted-foreground sm:block">
              {me.data?.email}
            </span>
            <ThemeToggle />
            <Button
              variant="ghost"
              size="sm"
              onClick={() => logout.mutate()}
              disabled={logout.isPending}
            >
              <LogOut className="size-4" aria-hidden />
              Sign out
            </Button>
          </div>
        </header>

        {menuOpen ? (
          <div className="border-b bg-sidebar p-4 md:hidden">
            <NavLinks onNavigate={() => setMenuOpen(false)} />
          </div>
        ) : null}

        <main className="mx-auto w-full max-w-7xl flex-1 p-4 md:p-8">{children}</main>
      </div>
    </div>
  );
}
