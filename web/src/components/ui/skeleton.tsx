import * as React from "react"
import { cn } from "@/lib/utils"

/** A placeholder in the shape of content that is loading. Hidden from screen readers:
 * the `Loading` around it says "Loading…" once. */
function Skeleton({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="skeleton"
      aria-hidden
      className={cn("skeleton", className)}
      {...props}
    />
  )
}

export { Skeleton }
