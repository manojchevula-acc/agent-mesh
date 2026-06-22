import { Landmark } from "lucide-react";
import { cn } from "@/lib/utils";

/** GERNAS brand mark + wordmark. */
export function Logo({
  className,
  iconClassName,
  showText = true,
  subtitle = "FAB Policy Assistant",
}: {
  className?: string;
  iconClassName?: string;
  showText?: boolean;
  subtitle?: string;
}) {
  return (
    <div className={cn("flex items-center gap-3", className)}>
      <div
        className={cn(
          "flex h-10 w-10 items-center justify-center rounded-xl bg-gradient-to-br from-brand-600 to-accent-500 text-white shadow-lg shadow-brand-600/20",
          iconClassName,
        )}
      >
        <Landmark className="h-5 w-5" />
      </div>
      {showText && (
        <div className="leading-tight">
          <p className="text-sm font-bold text-fg">GERNAS RAG</p>
          <p className="text-xs text-muted">{subtitle}</p>
        </div>
      )}
    </div>
  );
}
