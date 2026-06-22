import { useHealth } from "@/hooks/useSystem";
import { cn } from "@/lib/utils";

/** Compact API connectivity indicator driven by the /health poll. */
export function ApiStatus({ compact = false }: { compact?: boolean }) {
  const { data, isLoading, isError } = useHealth();
  const online = !isError && data?.status === "ok";

  const tone = isLoading ? "bg-slate-400" : online ? "bg-emerald-500" : "bg-red-500";
  const label = isLoading ? "Checking…" : online ? "API online" : "API offline";

  return (
    <div
      className={cn(
        "flex items-center gap-2 rounded-lg px-2.5 py-1.5",
        compact ? "" : "bg-surface-2 ring-1 ring-inset ring-line",
      )}
      title={online ? "Backend reachable" : "Backend not reachable"}
    >
      <span className="relative flex h-2.5 w-2.5">
        {online && (
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-75" />
        )}
        <span className={cn("relative inline-flex h-2.5 w-2.5 rounded-full", tone)} />
      </span>
      <span className={cn("text-xs font-medium", online ? "text-emerald-600 dark:text-emerald-400" : "text-muted")}>
        {label}
      </span>
    </div>
  );
}
