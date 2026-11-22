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

/** Where keys belong to something else: a field, a dialog, an open menu or list. */
export const NOT_REVIEW_KEYS =
  "input, textarea, select, [role=dialog], [role=alertdialog], [role=menu], [role=listbox]";

/** Keys typed into a field, an editable region, a dialog or an open menu are not shortcuts. */
function typing(target: EventTarget | null): boolean {
  if (!(target instanceof Element)) return false;
  if (target.closest(NOT_REVIEW_KEYS)) return true;
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
