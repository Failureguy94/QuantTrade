import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

const TONES: Record<string, string> = {
  COMPLETED: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300",
  FILLED: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300",
  RUNNING: "bg-blue-500/15 text-blue-700 dark:text-blue-300",
  OPEN: "bg-blue-500/15 text-blue-700 dark:text-blue-300",
  QUEUED: "bg-amber-500/15 text-amber-700 dark:text-amber-300",
  PENDING: "bg-amber-500/15 text-amber-700 dark:text-amber-300",
  PARTIALLY_FILLED: "bg-amber-500/15 text-amber-700 dark:text-amber-300",
  FAILED: "bg-red-500/15 text-red-700 dark:text-red-300",
  REJECTED: "bg-red-500/15 text-red-700 dark:text-red-300",
  CANCELLED: "bg-muted text-muted-foreground",
};

export function StatusBadge({ status }: { status: string }) {
  return (
    <Badge variant="secondary" className={cn("font-medium", TONES[status])}>
      {status.replace(/_/g, " ").toLowerCase()}
    </Badge>
  );
}
