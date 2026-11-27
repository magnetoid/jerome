import { useLayoutEffect, useRef, type RefObject } from "react";

/** What counts as "the same page" for the page-enter animation: the pathname, with the
 * chapter left out on Review and Preview, so moving between chapters does not replay it. */
export function routeKey(pathname: string): string {
  const chapter = pathname.match(/^(\/projects\/[^/]+\/(?:review|preview))\/[^/]+\/?$/);
  return chapter ? chapter[1] : pathname;
}

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
  useLayoutEffect(() => {
    if (shown.current === key) return;
    shown.current = key;
    const el = ref.current;
    if (!el) return;
    el.classList.remove(className);
    void el.offsetWidth;
    el.classList.add(className);
  }, [key, className]);
  return ref;
}
