import { cn } from "@/lib/utils";

/** Jerome's mark, a serif J on a gilt square, with the name beside it unless `wordmark` is off. */
export default function Logo({ wordmark = true, className }: { wordmark?: boolean; className?: string }) {
  return (
    <span className={cn("inline-flex items-center gap-2.5", className)}>
      <span
        data-logo-mark
        aria-hidden
        className="grid size-7 shrink-0 place-items-center rounded-md bg-brand font-display text-[1.35rem] leading-none text-brand-foreground"
      >
        J
      </span>
      {wordmark && <span className="font-display text-[1.35rem] leading-none tracking-tight">Jerome</span>}
    </span>
  );
}
