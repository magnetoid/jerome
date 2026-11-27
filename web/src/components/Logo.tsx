import { cn } from "@/lib/utils";

/** Jerome's mark, a serif J on a gilt square, with the name beside it unless `wordmark` is
 * off. `wordmarkClassName` styles the name alone, for places that hide it on small screens. */
export default function Logo({ wordmark = true, size = "md", className, wordmarkClassName }: {
  wordmark?: boolean;
  size?: "md" | "lg";
  className?: string;
  wordmarkClassName?: string;
}) {
  const lg = size === "lg";
  return (
    <span className={cn("inline-flex items-center gap-2.5", className)}>
      <span
        data-logo-mark
        aria-hidden
        className={cn(
          "grid shrink-0 place-items-center bg-brand font-display leading-none text-brand-foreground",
          lg ? "size-10 rounded-lg text-3xl" : "size-7 rounded-md text-[1.35rem]",
        )}
      >
        J
      </span>
      {wordmark && (
        <span className={cn("font-display leading-none tracking-tight", lg ? "text-4xl" : "text-[1.35rem]", wordmarkClassName)}>
          Jerome
        </span>
      )}
    </span>
  );
}
