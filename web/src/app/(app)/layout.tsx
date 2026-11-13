import { Suspense } from "react";

import { AppShell } from "@/components/app-shell";
import { LoadingRows } from "@/components/states";

export default function AppLayout({ children }: { children: React.ReactNode }) {
  return (
    <AppShell>
      {/* Pages read route params and the query string, known only at request time. */}
      <Suspense fallback={<LoadingRows rows={6} />}>{children}</Suspense>
    </AppShell>
  );
}
