import type { ReactNode } from "react";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

/** A loading state: skeleton shapes for the eye, and "Loading…" once for screen readers.
 * Give it shapes that look like what is coming; without any it shows three lines. */
export default function Loading({ children, className }: { children?: ReactNode; className?: string }) {
  return (
    <div role="status" className={cn("space-y-3", className)}>
      <span className="sr-only">Loading…</span>
      {children ?? (
        <>
          <Skeleton className="h-4 w-2/3" />
          <Skeleton className="h-4 w-full" />
          <Skeleton className="h-4 w-1/2" />
        </>
      )}
    </div>
  );
}
