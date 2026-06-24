import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { cn } from "@/lib/utils";

/** Renders RAG answer / chunk text as GitHub-flavoured markdown. */
export function Markdown({ children, className }: { children: string; className?: string }) {
  return (
    <div className={cn("prose-rag", className)}>
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{children}</ReactMarkdown>
    </div>
  );
}
