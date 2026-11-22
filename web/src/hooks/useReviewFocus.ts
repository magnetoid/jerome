import { useMemo, useState } from "react";
import { compareItems, type Item } from "@/lib/review";

/**
 * The focused suggestion in the current view. After a refetch or a view change the focus
 * stays on the same suggestion while it is still in the view; otherwise it moves on to the
 * next one in document order (or the last one, at the end of the chapter).
 */
export function useReviewFocus(items: Item[]) {
  const [anchor, setAnchor] = useState<Item | null>(null);

  const current = useMemo(() => {
    if (!anchor || items.length === 0) return null;
    return (
      items.find((it) => it.s.id === anchor.s.id) ??
      items.find((it) => compareItems(it, anchor) > 0) ??
      items[items.length - 1]
    );
  }, [items, anchor]);

  const index = current ? items.indexOf(current) : -1;

  return {
    current,
    /** Focus an item, or nothing (stable across renders). */
    focus: setAnchor,
    /** Moves to the next item; false when it cannot move (on the last one, or none). */
    next: () => {
      const it = items[index + 1];
      if (it) setAnchor(it);
      return it !== undefined;
    },
    /** Moves to the previous item; false when it cannot move. */
    prev: () => {
      const it = index < 0 ? items[0] : items[index - 1];
      if (it) setAnchor(it);
      return it !== undefined && it !== current;
    },
  };
}
