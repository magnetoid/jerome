import { useEffect, useRef } from "react";

export interface ReviewKeyHandlers {
  accept: () => void;
  reject: () => void;
  edit: () => void;
  next: () => void;
  prev: () => void;
  undo: () => void;
  escape: () => void;
}

const KEYS: Record<string, keyof ReviewKeyHandlers> = {
  a: "accept",
  r: "reject",
  e: "edit",
  j: "next",
  k: "prev",
  u: "undo",
  escape: "escape",
};

/** Keys typed into a field, a select, an editable region or a dialog are not shortcuts. */
function typing(target: EventTarget | null): boolean {
  if (!(target instanceof Element)) return false;
  if (target.closest("input, textarea, select, [role=dialog], [role=alertdialog]")) return true;
  const editable = target.closest("[contenteditable]");
  return editable !== null && editable.getAttribute("contenteditable") !== "false";
}

/** The review screen's single-key shortcuts: a r e j k u, and Escape. */
export function useReviewKeys(handlers: ReviewKeyHandlers) {
  const latest = useRef(handlers);
  useEffect(() => {
    latest.current = handlers;
  });
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented || e.ctrlKey || e.metaKey || e.altKey || typing(e.target)) return;
      const name = KEYS[e.key.toLowerCase()];
      if (!name) return;
      // Holding a decision key down must not fire it again and again.
      if (e.repeat && name !== "next" && name !== "prev") return;
      // Stops the key from also being typed into a field it opens (e opens the edit field).
      e.preventDefault();
      latest.current[name]();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
}
