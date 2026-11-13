import { Suspense } from "react";
import Link from "next/link";
import { LineChart } from "lucide-react";

import { ThemeToggle } from "@/components/app-shell";

export default function AuthLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-screen flex-col">
      <header className="flex items-center justify-between p-4">
        <Link href="/" className="flex items-center gap-2 font-semibold tracking-tight">
          <span className="grid size-7 place-items-center rounded-md bg-primary text-primary-foreground">
            <LineChart className="size-4" aria-hidden />
          </span>
          QuantTrade
        </Link>
        <ThemeToggle />
      </header>
      <main className="flex flex-1 items-center justify-center p-4">
        <div className="w-full max-w-sm">
          {/* The forms read the query string, which is only known in the browser. */}
          <Suspense>{children}</Suspense>
        </div>
      </main>
    </div>
  );
}
