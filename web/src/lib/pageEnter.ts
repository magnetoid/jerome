import { useLayoutEffect, useRef, type RefObject } from "react";

/** What counts as "the same page" for the page-enter animation: the pathname, with the
 * chapter left out on Review and Preview, so moving between chapters does not replay it. */
export function routeKey(pathname: string): string {
  const chapter = pathname.match(/^(\/projects\/[^/]+\/(?:review|preview))\/[^/]+\/?$/);
  return chapter ? chapter[1] : pathname;
}

/** Changes closer together than this do not replay the animation: a person holding j to
 * travel through suggestions must see a steady card, not one that keeps fading in. */
const REST_MS = 150;

/** A ref for an element that carries a CSS enter animation as `className`. When `key`
 * changes, the animation plays again on that same element: the class comes off, a layout
 * read makes the browser notice, and the class goes back. Nothing is remounted, so no state
 * under the element is lost. The first render plays through the class alone. */
export function usePageEnter<T extends HTMLElement = HTMLDivElement>(
  key: string,
  className = "page-enter",
): RefObject<T | null> {
  const ref = useRef<T>(null);
  const shown = useRef(key);
  const changedAt = useRef(Number.NEGATIVE_INFINITY);
  useLayoutEffect(() => {
    if (shown.current === key) return;
    shown.current = key;
    const now = performance.now();
    const quick = now - changedAt.current < REST_MS;
    changedAt.current = now;
    if (quick) return;
    const el = ref.current;
    if (!el) return;
    el.classList.remove(className);
    void el.offsetWidth;
    el.classList.add(className);
  }, [key, className]);
  return ref;
}
