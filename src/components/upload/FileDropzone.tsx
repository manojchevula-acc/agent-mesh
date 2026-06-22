import { useRef, useState, type DragEvent } from "react";
import { FileText, UploadCloud, X } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { cn, formatBytes } from "@/lib/utils";

const ACCEPT = ".pdf,.docx";

export function FileDropzone({
  file,
  onSelect,
  disabled,
}: {
  file: File | null;
  onSelect: (file: File | null) => void;
  disabled?: boolean;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);

  function handleDrop(e: DragEvent) {
    e.preventDefault();
    setDragging(false);
    if (disabled) return;
    const dropped = e.dataTransfer.files?.[0];
    if (dropped) onSelect(dropped);
  }

  if (file) {
    return (
      <div className="flex items-center justify-between rounded-xl border border-line bg-surface-2 px-4 py-3">
        <div className="flex min-w-0 items-center gap-3">
          <FileText className="h-8 w-8 shrink-0 text-brand-600 dark:text-brand-300" />
          <div className="min-w-0">
            <p className="truncate text-sm font-medium text-fg">{file.name}</p>
            <p className="text-xs text-faint">{formatBytes(file.size)}</p>
          </div>
        </div>
        <Button variant="ghost" size="sm" onClick={() => onSelect(null)} disabled={disabled}>
          <X className="h-4 w-4" /> Remove
        </Button>
      </div>
    );
  }

  return (
    <div
      onDragOver={(e) => {
        e.preventDefault();
        if (!disabled) setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={handleDrop}
      onClick={() => !disabled && inputRef.current?.click()}
      className={cn(
        "flex cursor-pointer flex-col items-center justify-center rounded-xl border-2 border-dashed px-6 py-10 text-center transition-colors",
        dragging
          ? "border-brand-500 bg-brand-50 dark:bg-brand-500/10"
          : "border-line bg-surface hover:border-brand-400",
        disabled && "cursor-not-allowed opacity-60",
      )}
    >
      <UploadCloud className="mb-3 h-10 w-10 text-faint" />
      <p className="text-sm font-medium text-fg">
        Drag & drop a file here, or <span className="text-brand-700 dark:text-brand-300">browse</span>
      </p>
      <p className="mt-1 text-xs text-faint">PDF or DOCX · large files (50+ pages) take 2–5 min</p>
      <input
        ref={inputRef}
        type="file"
        accept={ACCEPT}
        className="hidden"
        disabled={disabled}
        onChange={(e) => onSelect(e.target.files?.[0] ?? null)}
      />
    </div>
  );
}
