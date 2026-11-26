import { toBlob } from "html-to-image";

// Overlays that must not appear in the picture: menus, dialogs and toasts.
const SKIP = '[data-slot^="dropdown-menu"], [data-slot^="dialog"], [data-radix-popper-content-wrapper], [data-sonner-toaster]';

/**
 * Sticky elements (the sidebar) sit at the top of the copy html-to-image lays out, which is
 * never scrolled. Pin each one, for the time of the capture, as position: relative at the
 * offset where it shows now, so the copy matches the screen. Returns the undo.
 */
function pinSticky(): () => void {
  const undo: Array<() => void> = [];
  for (const el of Array.from(document.body.querySelectorAll<HTMLElement>("*"))) {
    if (getComputedStyle(el).position !== "sticky") continue;
    const { position, top } = el.style;
    const shown = el.getBoundingClientRect().top;
    el.style.position = "relative";
    el.style.top = "0px";
    const natural = el.getBoundingClientRect().top;
    el.style.top = `${shown - natural}px`;
    undo.push(() => {
      el.style.position = position;
      el.style.top = top;
    });
  }
  return () => undo.forEach((u) => u());
}

/** A PNG of what is on screen now (the visible part of the page), or null if it fails. */
export async function captureScreen(): Promise<Blob | null> {
  const body = document.body;
  const unpin = pinSticky();
  try {
    const background = getComputedStyle(body).backgroundColor;
    return await toBlob(body, {
      width: window.innerWidth,
      height: window.innerHeight,
      pixelRatio: Math.min(window.devicePixelRatio || 1, 2),
      backgroundColor: background && background !== "rgba(0, 0, 0, 0)" ? background : "#ffffff",
      skipFonts: true,
      cacheBust: false,
      style: { transform: `translate(${-window.scrollX}px, ${-window.scrollY}px)`, margin: "0" },
      filter: (node) => !(node instanceof Element && node.matches(SKIP)),
    });
  } catch {
    return null;
  } finally {
    unpin();
  }
}
