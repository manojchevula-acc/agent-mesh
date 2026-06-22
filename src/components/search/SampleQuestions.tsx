import { useState } from "react";
import { ChevronDown, Lightbulb } from "lucide-react";
import { SAMPLE_QUESTIONS } from "@/config/constants";
import { cn } from "@/lib/utils";

export function SampleQuestions({ onPick }: { onPick: (q: string) => void }) {
  const [openCategory, setOpenCategory] = useState<string | null>(SAMPLE_QUESTIONS[0]?.category ?? null);

  return (
    <div>
      <div className="mb-2 flex items-center gap-2 text-sm font-semibold text-fg">
        <Lightbulb className="h-4 w-4 text-amber-500" />
        Sample questions
      </div>
      <div className="space-y-1.5">
        {SAMPLE_QUESTIONS.map(({ category, questions }) => {
          const open = openCategory === category;
          return (
            <div key={category} className="overflow-hidden rounded-lg border border-line">
              <button
                onClick={() => setOpenCategory(open ? null : category)}
                className="flex w-full items-center justify-between bg-surface-2 px-3 py-2 text-left text-sm font-medium text-fg transition-colors hover:bg-line/50"
              >
                {category}
                <ChevronDown
                  className={cn("h-4 w-4 text-faint transition-transform", open && "rotate-180")}
                />
              </button>
              {open && (
                <div className="animate-fade-in space-y-1 p-2">
                  {questions.map((q) => (
                    <button
                      key={q}
                      onClick={() => onPick(q)}
                      className="block w-full rounded-md px-2.5 py-2 text-left text-xs leading-snug text-muted transition-colors hover:bg-brand-50 hover:text-brand-800 dark:hover:bg-brand-500/10 dark:hover:text-brand-200"
                    >
                      {q}
                    </button>
                  ))}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
