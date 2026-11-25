import { useCallback, useMemo, useState } from "react";
import { compareItems, type Item } from "@/lib/review";

/**
 * The focused suggestion in the current view. After a refetch or a view change the focus
 * stays on the same suggestion while it is still in the view; otherwise it moves on to the
 * next one in document order (or the last one, at the end of the chapter).
 *
 * `pickable` holds suggestions outside the view that a person may still click (decided
 * marks in Needs you). One that is picked stays focused while the decision it was picked
 * with stands; j/k then move to the view's next or previous suggestion in document order.
 */
export function useReviewFocus(items: Item[], pickable: Item[] = []) {
  const [anchor, setAnchor] = useState<{ item: Item; picked: boolean } | null>(null);

  const current = useMemo(() => {
    if (!anchor) return null;
    const id = anchor.item.s.id;
    const inView = items.find((it) => it.s.id === id);
    if (inView) return inView;
    if (anchor.picked) {
      // Only while the decision it was picked with stands: once it is reverted and decided
      // again (here or by someone else), it is an ordinary mark the focus moves on from.
      const was = anchor.item.s;
      const picked = pickable.find(
        (it) => it.s.id === id && it.s.status === was.status && it.s.decided_at === was.decided_at,
      );
      if (picked) return picked;
    }
    if (items.length === 0) return null;
    return items.find((it) => compareItems(it, anchor.item) > 0) ?? items[items.length - 1];
  }, [items, pickable, anchor]);

  const focus = useCallback((item: Item | null) => setAnchor(item && { item, picked: false }), []);
  const pick = useCallback((item: Item) => setAnchor({ item, picked: true }), []);
  const index = current ? items.indexOf(current) : -1;
  // From a picked suggestion outside the view, its neighbours in document order.
  const after = () => (current ? items.find((it) => compareItems(it, current) > 0) : items[0]);
  const before = () => (current ? items.findLast((it) => compareItems(it, current) < 0) : items[0]);

  return {
    current,
    /** Focus an item of the view, or nothing (stable across renders). */
    focus,
    /** Focus a pickable item outside the view (stable across renders). */
    pick,
    /** Moves to the next item; false when it cannot move (on the last one, or none). */
    next: () => {
      const it = index < 0 ? after() : items[index + 1];
      if (it) setAnchor({ item: it, picked: false });
      return it !== undefined;
    },
    /** Moves to the previous item; false when it cannot move. */
    prev: () => {
      const it = index < 0 ? before() : items[index - 1];
      if (it) setAnchor({ item: it, picked: false });
      return it !== undefined && it !== current;
    },
  };
}
