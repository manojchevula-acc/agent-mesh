import type { ReactNode } from "react";
import { AlertTriangle, CheckCircle2, Info, XCircle } from "lucide-react";
import { cn } from "@/lib/utils";

type Variant = "info" | "success" | "warning" | "error";

const config: Record<Variant, { wrap: string; icon: ReactNode }> = {
  info: {
    wrap: "bg-brand-50 border-brand-200 text-brand-900 dark:bg-brand-500/10 dark:border-brand-500/30 dark:text-brand-100",
    icon: <Info className="h-5 w-5 text-brand-600 dark:text-brand-300" />,
  },
  success: {
    wrap: "bg-emerald-50 border-emerald-200 text-emerald-900 dark:bg-emerald-500/10 dark:border-emerald-500/30 dark:text-emerald-100",
    icon: <CheckCircle2 className="h-5 w-5 text-emerald-600 dark:text-emerald-400" />,
  },
  warning: {
    wrap: "bg-amber-50 border-amber-200 text-amber-900 dark:bg-amber-500/10 dark:border-amber-500/30 dark:text-amber-100",
    icon: <AlertTriangle className="h-5 w-5 text-amber-600 dark:text-amber-400" />,
  },
  error: {
    wrap: "bg-red-50 border-red-200 text-red-900 dark:bg-red-500/10 dark:border-red-500/30 dark:text-red-100",
    icon: <XCircle className="h-5 w-5 text-red-600 dark:text-red-400" />,
  },
};

export function Alert({
  variant = "info",
  title,
  children,
  className,
}: {
  variant?: Variant;
  title?: ReactNode;
  children?: ReactNode;
  className?: string;
}) {
  const c = config[variant];
  return (
    <div className={cn("flex gap-3 rounded-lg border px-4 py-3 text-sm", c.wrap, className)}>
      <div className="mt-0.5 shrink-0">{c.icon}</div>
      <div className="min-w-0 space-y-1">
        {title && <p className="font-semibold">{title}</p>}
        {children && <div className="leading-relaxed [overflow-wrap:anywhere]">{children}</div>}
      </div>
    </div>
  );
}
