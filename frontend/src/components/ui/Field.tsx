import {
  forwardRef,
  type InputHTMLAttributes,
  type ReactNode,
  type SelectHTMLAttributes,
  type TextareaHTMLAttributes,
} from "react";
import { cn } from "@/lib/utils";

export function Label({ children, htmlFor }: { children: ReactNode; htmlFor?: string }) {
  return (
    <label htmlFor={htmlFor} className="mb-1.5 block text-sm font-medium text-fg">
      {children}
    </label>
  );
}

export function Hint({ children }: { children: ReactNode }) {
  return <p className="mt-1 text-xs text-faint">{children}</p>;
}

const fieldBase =
  "w-full rounded-lg border border-line bg-surface px-3 text-sm text-fg placeholder:text-faint transition-colors focus:border-brand-500 disabled:bg-surface-2 disabled:text-faint";

export const TextInput = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(
  function TextInput({ className, ...props }, ref) {
    return <input ref={ref} className={cn(fieldBase, "h-10", className)} {...props} />;
  },
);

export const TextArea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement>>(
  function TextArea({ className, ...props }, ref) {
    return <textarea ref={ref} className={cn(fieldBase, "py-2 leading-relaxed resize-y", className)} {...props} />;
  },
);

export interface SelectOption {
  label: string;
  value: string;
}

export const Select = forwardRef<
  HTMLSelectElement,
  SelectHTMLAttributes<HTMLSelectElement> & { options: SelectOption[] }
>(function Select({ className, options, ...props }, ref) {
  return (
    <select ref={ref} className={cn(fieldBase, "h-10 cursor-pointer", className)} {...props}>
      {options.map((o) => (
        <option key={o.value} value={o.value}>
          {o.label}
        </option>
      ))}
    </select>
  );
});
