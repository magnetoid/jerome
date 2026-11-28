"use client"

import * as React from "react"
import { cn } from "@/lib/utils"
import { Progress as ProgressPrimitive } from "radix-ui"

/** `running` marks work that is still going: the filled part carries a slow shimmer. */
function Progress({
  className,
  value,
  running = false,
  ...props
}: React.ComponentProps<typeof ProgressPrimitive.Root> & { running?: boolean }) {
  return (
    <ProgressPrimitive.Root
      data-slot="progress"
      data-running={running ? "true" : undefined}
      value={value}
      className={cn(
        "relative h-2 w-full overflow-hidden rounded-full bg-muted",
        className
      )}
      {...props}
    >
      <ProgressPrimitive.Indicator
        data-slot="progress-indicator"
        className={cn(
          "h-full rounded-full bg-brand transition-[width] duration-300 ease-out",
          running && "shimmer"
        )}
        style={{ width: `${Math.min(100, Math.max(0, value || 0))}%` }}
      />
    </ProgressPrimitive.Root>
  )
}

export { Progress }
