import { cn } from "@/lib/utils";

export function Slider({
  value,
  min,
  max,
  step = 1,
  onChange,
  label,
}: {
  value: number;
  min: number;
  max: number;
  step?: number;
  onChange: (next: number) => void;
  label?: string;
}) {
  return (
    <div>
      {label && (
        <div className="mb-1.5 flex items-center justify-between">
          <span className="text-sm font-medium text-fg">{label}</span>
          <span className="rounded-md bg-brand-50 px-2 py-0.5 text-sm font-semibold text-brand-700 tabular-nums dark:bg-brand-500/15 dark:text-brand-200">
            {value}
          </span>
        </div>
      )}
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className={cn(
          "h-2 w-full cursor-pointer appearance-none rounded-full bg-surface-2 accent-brand-600",
        )}
      />
    </div>
  );
}
