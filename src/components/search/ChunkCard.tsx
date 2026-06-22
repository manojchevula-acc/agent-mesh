import { AlertTriangle, Calendar, FileText, Hash } from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { Markdown } from "@/components/ui/Markdown";
import { Tabs } from "@/components/ui/Tabs";
import { cn } from "@/lib/utils";
import type { RetrievedChunk } from "@/types/api";

export function ChunkCard({ chunk, rank }: { chunk: RetrievedChunk; rank: number }) {
  const stale = chunk.freshness_warning;
  const hasParent =
    chunk.parent_text != null && chunk.parent_text.trim() !== chunk.text.trim();

  return (
    <div
      className={cn(
        "overflow-hidden rounded-xl border bg-surface shadow-sm transition-all hover:shadow-md",
        stale ? "border-amber-300 dark:border-amber-500/40" : "border-line",
      )}
    >
      {/* Header strip */}
      <div
        className={cn(
          "flex flex-wrap items-center gap-2 border-l-4 px-4 py-3",
          stale
            ? "border-l-amber-500 bg-amber-50/60 dark:bg-amber-500/10"
            : "border-l-brand-600 bg-surface-2",
        )}
      >
        <Badge tone="brand">
          <Hash className="h-3 w-3" /> #{rank} · {chunk.score.toFixed(3)}
        </Badge>
        <Badge tone="slate">
          <FileText className="h-3 w-3" /> {chunk.source}
        </Badge>
        {chunk.clause_reference && (
          <Badge tone="green">§ {chunk.clause_reference}</Badge>
        )}
        {chunk.effective_date && (
          <Badge tone="slate">
            <Calendar className="h-3 w-3" /> {chunk.effective_date}
          </Badge>
        )}
        {stale && (
          <Badge tone="amber">
            <AlertTriangle className="h-3 w-3" /> Stale
          </Badge>
        )}
      </div>

      {/* Body */}
      <div className="px-4 py-4">
        {hasParent ? (
          <Tabs
            items={[
              {
                id: "child",
                label: `Matched chunk · ${chunk.text.length} chars`,
                content: <Markdown>{chunk.text}</Markdown>,
              },
              {
                id: "parent",
                label: `Parent section · ${chunk.parent_text!.length} chars (sent to LLM)`,
                content: <Markdown>{chunk.parent_text!}</Markdown>,
              },
            ]}
          />
        ) : (
          <Markdown>{chunk.text}</Markdown>
        )}
      </div>
    </div>
  );
}
