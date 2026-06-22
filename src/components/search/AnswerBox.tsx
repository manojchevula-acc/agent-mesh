import { Sparkles } from "lucide-react";
import { Markdown } from "@/components/ui/Markdown";

export function AnswerBox({ answer }: { answer: string }) {
  return (
    <div className="animate-fade-in-up rounded-xl border border-brand-200 bg-gradient-to-br from-brand-50 to-surface p-5 shadow-sm dark:border-brand-500/30 dark:from-brand-500/10 dark:to-surface">
      <div className="mb-2 flex items-center gap-2 text-brand-800 dark:text-brand-200">
        <Sparkles className="h-4 w-4" />
        <h3 className="text-sm font-semibold">Generated Answer</h3>
      </div>
      <Markdown>{answer}</Markdown>
    </div>
  );
}
