import { cn } from "@/lib/utils";
import type { TokenUsageSummary } from "@/types/mesh";

interface CostBadgeProps {
  tokenUsage: TokenUsageSummary;
  className?: string;
}

export default function CostBadge({ tokenUsage, className }: CostBadgeProps) {
  const { total_input_tokens, total_output_tokens, estimated_usd } = tokenUsage;
  if (!total_input_tokens && !total_output_tokens) return null;

  const costStr =
    estimated_usd > 0
      ? estimated_usd < 0.000001
        ? `<$0.000001`
        : `~$${estimated_usd.toFixed(6)}`
      : null;

  return (
    <div className={cn("flex flex-wrap items-center gap-1", className)}>
      <span className="inline-flex items-center gap-1.5 text-xs px-2 py-0.5 rounded-full bg-slate-100 dark:bg-slate-800 text-slate-500 dark:text-slate-400 font-mono">
        <span title="Prompt tokens" className="text-emerald-600 dark:text-emerald-400">
          ↑
        </span>
        {total_input_tokens.toLocaleString()}
        <span className="text-slate-300 dark:text-slate-600">·</span>
        <span title="Completion tokens" className="text-blue-600 dark:text-blue-400">
          ↓
        </span>
        {total_output_tokens.toLocaleString()}
        {" tok"}
        {costStr && (
          <>
            <span className="text-slate-300 dark:text-slate-600">·</span>
            <span className="text-amber-600 dark:text-amber-400">{costStr}</span>
          </>
        )}
      </span>
    </div>
  );
}
