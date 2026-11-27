import { useMemo } from "react";
import { useInfiniteQuery } from "@tanstack/react-query";
import { apiFetch } from "@/lib/api";

export type PreviewState = "original" | "translation" | "proofread";
export type PreviewView = "text" | "book";
export type PreviewLayout = "single" | "side";
export type Prefs = { view: PreviewView; layout: PreviewLayout; states: PreviewState[]; changes: boolean };

export type MarkJson = { start: number; end: number; kind: string; href: string | null };
export type Span = { start: number; end: number; removed: string };
export type StateText = { text: string; marks: MarkJson[]; changes?: Span[]; status?: string };

export type PreviewBlock = {
  id: number;
  ordinal: number;
  kind: string;
  original: StateText | null;
  translation: StateText | null;
  proofread: StateText;
};

export type PreviewPageData = {
  chapter_id: number;
  book_id: number;
  title: string;
  proofread: boolean;
  total_blocks: number;
  offset: number;
  work: "proofread" | "translate";
  variant: string | null;
  source_variant: string | null;
  trim_size: string | null;
  blocks: PreviewBlock[];
};

/** Every state in reading order: columns always show in this order. */
export const STATES: PreviewState[] = ["original", "translation", "proofread"];
export const STATE_LABELS: Record<PreviewState, string> = {
  original: "Original",
  translation: "Translation",
  proofread: "Proofread",
};
export const DEFAULT_PREFS: Prefs = {
  view: "text",
  layout: "side",
  states: ["original", "proofread"],
  changes: true,
};
export const PREVIEW_PAGE = 100;
const STORAGE_KEY = "jerome.preview";

export const previewKey = (chapterId: string) => ["preview", chapterId] as const;

/** A chapter's paragraphs in their three states, PREVIEW_PAGE paragraphs per page. */
export function usePreview(chapterId: string) {
  const query = useInfiniteQuery({
    queryKey: previewKey(chapterId),
    queryFn: ({ pageParam }) =>
      apiFetch<PreviewPageData>(
        `/v1/chapters/${chapterId}/preview?offset=${pageParam}&limit=${PREVIEW_PAGE}`,
      ),
    initialPageParam: 0,
    getNextPageParam: (last) => {
      const next = last.offset + last.blocks.length;
      return last.blocks.length > 0 && next < last.total_blocks ? next : undefined;
    },
  });
  const blocks = useMemo(() => query.data?.pages.flatMap((p) => p.blocks) ?? [], [query.data]);
  return { query, blocks, first: query.data?.pages[0] };
}

const isState = (s: string): s is PreviewState => (STATES as string[]).includes(s);

function parseStates(raw: unknown): PreviewState[] | undefined {
  const list = typeof raw === "string" ? raw.split(",") : Array.isArray(raw) ? raw : undefined;
  if (!list) return undefined;
  const states = list.filter((s): s is PreviewState => typeof s === "string" && isState(s));
  return states.length ? states : undefined;
}

/** Whatever valid prefs a source holds; the rest is left out. */
function partial(src: Record<string, unknown>): Partial<Prefs> {
  const out: Partial<Prefs> = {};
  if (src.view === "text" || src.view === "book") out.view = src.view;
  if (src.layout === "single" || src.layout === "side") out.layout = src.layout;
  const states = parseStates(src.states);
  if (states) out.states = states;
  if (src.changes === "1" || src.changes === true) out.changes = true;
  if (src.changes === "0" || src.changes === false) out.changes = false;
  return out;
}

export function readPrefs(): Partial<Prefs> {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    return raw ? partial(JSON.parse(raw) as Record<string, unknown>) : {};
  } catch {
    return {};
  }
}

export function writePrefs(prefs: Prefs): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(prefs));
  } catch {
    // private window or blocked storage: the address still holds the choice
  }
}

/** The prefs to show: the address first, then the last choice kept, then the defaults. */
export function resolvePrefs(search: URLSearchParams, stored: Partial<Prefs>, translated: boolean): Prefs {
  const fromUrl = partial(Object.fromEntries(search.entries()));
  return normalize({ ...DEFAULT_PREFS, ...stored, ...fromUrl }, translated);
}

/** Make a state list fit the layout and the book: one in Single, two or three side by side,
 * and no Translation for a proofread-only book. */
export function normalize(prefs: Prefs, translated: boolean): Prefs {
  const offered = STATES.filter((s) => translated || s !== "translation");
  let states = STATES.filter((s) => prefs.states.includes(s) && offered.includes(s));
  if (prefs.layout === "single") {
    states = [states[0] ?? DEFAULT_PREFS.states[0]];
  } else {
    for (const s of DEFAULT_PREFS.states) if (states.length < 2 && !states.includes(s)) states.push(s);
    states = STATES.filter((s) => states.includes(s));
  }
  return { ...prefs, states };
}

export function prefsSearch(prefs: Prefs): string {
  return new URLSearchParams({
    view: prefs.view,
    layout: prefs.layout,
    states: prefs.states.join(","),
    changes: prefs.changes ? "1" : "0",
  }).toString().replace(/%2C/g, ",");
}
