// Review screen data: API shapes, query keys, views and how marks are laid out in a block.
import type { ChapterSummary } from "@/lib/jobs";

export type Status = "open" | "accepted" | "rejected" | "edited" | "superseded";
export type Category = "typo" | "grammar" | "punctuation" | "consistency" | "style";
export type Severity = "high" | "medium" | "low";

export interface Suggestion {
  id: number;
  book_id: number;
  block_id: number;
  chapter_id: number;
  /** Code-point offsets into the block's `original` text. */
  start: number;
  end: number;
  original: string;
  replacement: string;
  category: Category | string;
  severity: Severity | string;
  source: "model" | "languagetool" | "consistency" | "editor" | string;
  explanation: string;
  status: Status;
  final_text: string | null;
  job_id: number | null;
  model: string | null;
  created_at: string;
  agreed: boolean;
  disputed: boolean;
  alternative: string | null;
  rule: "house_spelling" | "quote_style" | "variant" | "name" | string | null;
  auto_applied: boolean;
  decided_by: string | null;
  /** Where the decision came from: "web", "api", "auto" or "mcp" (Janus). */
  decided_channel: string | null;
}

export interface ReviewBlock {
  id: number;
  ordinal: number;
  kind: string;
  original: string;
  current: string;
  suggestions: Suggestion[];
}

export interface ReviewPageData {
  chapter_id: number;
  book_id: number;
  total_blocks: number;
  offset: number;
  blocks: ReviewBlock[];
}

export const PAGE_SIZE = 50;

export const reviewKey = (bookId: string, chapterId?: string) =>
  chapterId === undefined ? (["review", bookId] as const) : (["review", bookId, chapterId] as const);
export const openCountKey = (bookId: string, chapterId?: string, category?: string) =>
  ["open-count", bookId, chapterId, category].filter((x) => x !== undefined);

export const CATEGORIES: Category[] = ["typo", "grammar", "punctuation", "consistency", "style"];
export const SEVERITIES: Severity[] = ["high", "medium", "low"];

export type View = "needs" | "ai" | "all";
export const VIEWS: { value: View; label: string }[] = [
  { value: "needs", label: "Needs you" },
  { value: "ai", label: "Applied by AI" },
  { value: "all", label: "All" },
];

export interface Filters {
  category: string;
  severity: string;
}

export function inView(s: Suggestion, view: View, f: Filters): boolean {
  if (s.status === "superseded") return false;
  if (view === "needs" && s.status !== "open") return false;
  if (view === "ai" && !s.auto_applied) return false;
  if (f.category && s.category !== f.category) return false;
  if (f.severity && s.severity !== f.severity) return false;
  return true;
}

/** A suggestion's place in document order: block ordinal, then start, then id. */
export interface Item {
  s: Suggestion;
  ordinal: number;
}

export function compareItems(a: Item, b: Item): number {
  return a.ordinal - b.ordinal || a.s.start - b.s.start || a.s.id - b.s.id;
}

/** The view's suggestions in document order, including ones not marked inline. */
export function viewItems(blocks: ReviewBlock[], view: View, f: Filters): Item[] {
  const items: Item[] = [];
  for (const b of blocks) {
    for (const s of b.suggestions) if (inView(s, view, f)) items.push({ s, ordinal: b.ordinal });
  }
  return items.sort(compareItems);
}

export const isDecided = (s: Suggestion) =>
  s.status === "accepted" || s.status === "edited" || s.status === "rejected";

/** The text a mark shows as its result: what stands in the book once decided. */
export const resultText = (s: Suggestion) => (s.status === "edited" ? (s.final_text ?? "") : s.replacement);

/** Same rule as the API's spans_overlap: touching spans do not overlap, an insertion
 * overlaps a span only strictly inside it, and two insertions at one point overlap. */
export function spansOverlap(a: { start: number; end: number }, b: { start: number; end: number }) {
  if (a.start === a.end && b.start === b.end) return a.start === b.start;
  return a.start < b.end && b.start < a.end;
}

/** Placement tiers (R29): changes in the text, then what needs a person, then the rest. */
const tier = (s: Suggestion) => (s.status === "accepted" || s.status === "edited" ? 0 : s.status === "open" ? 1 : 2);

/**
 * The suggestions marked inline in a block, in start order. Superseded ones are never
 * marked. Marks are placed tier by tier: accepted and edited first, so the text always reads
 * as it now stands; then open ones, so nothing that needs a person hides behind a rejected
 * one; then the rest (rejected). Within a tier they go in start order, and one that overlaps
 * a mark already placed is left out (it stays reachable with j/k and the focus card).
 */
export function layoutMarks(suggestions: Suggestion[]): Suggestion[] {
  const live = suggestions
    .filter((s) => s.status !== "superseded")
    .sort((a, b) => tier(a) - tier(b) || a.start - b.start || a.id - b.id);
  const placed: Suggestion[] = [];
  for (const s of live) {
    if (!placed.some((p) => spansOverlap(p, s))) placed.push(s);
  }
  return placed.sort((a, b) => a.start - b.start || a.end - b.end || a.id - b.id);
}

/** The mark that took a suggestion's place in the text, or null when it is marked itself. */
export function hiddenBy(suggestions: Suggestion[], s: Suggestion): Suggestion | null {
  const marks = layoutMarks(suggestions);
  if (marks.some((m) => m.id === s.id)) return null;
  return marks.find((m) => spansOverlap(m, s)) ?? null;
}

export const SOURCE_LABELS: Record<string, string> = {
  model: "AI",
  languagetool: "LanguageTool",
  consistency: "Consistency check",
  editor: "Editor",
};

export const RULE_LABELS: Record<string, string> = {
  house_spelling: "House spelling rule",
  quote_style: "House quote style rule",
  variant: "Spelling variant (majority form in the book)",
  name: "Name variant (majority form in the book)",
};

/** Who decided it, in words: "Needs you", "Applied by AI", "Accepted by …". */
export function stateLabel(s: Suggestion): string {
  if (s.status === "open") return "Needs you";
  if (s.auto_applied) return "Applied by AI";
  const verb = { accepted: "Accepted", edited: "Edited", rejected: "Rejected", superseded: "Superseded" }[s.status];
  // decided_by is only meaningful on a decided row (an undone row carries the undoer).
  if (!isDecided(s) || !s.decided_by) return verb;
  // The API key's name, for a decision Janus made through the MCP server.
  return s.decided_channel === "mcp" ? `${verb} by Janus (${s.decided_by})` : `${verb} by ${s.decided_by}`;
}

const quoted = (t: string) => (t ? `“${t}”` : "nothing");

/** "“teh” → “the”", with "nothing" for an insertion's original or a deletion's result. */
export const changeText = (s: Suggestion) => `${quoted(s.original)} → ${quoted(resultText(s))}`;

// Which actions a suggestion offers. The API stays the judge: a person's own accept still
// offers Reject, which the API refuses with a 409 whose message is shown (no silent undo).
export const canAccept = (s: Suggestion) => s.status === "open";
export const canReject = (s: Suggestion) =>
  s.status === "open" || s.status === "accepted" || s.status === "edited";
export const canEdit = (s: Suggestion) => s.status === "open" || s.auto_applied;

/** The chapter to open without one in the URL: the first with open items, else the first. */
export const defaultChapter = (chapters: ChapterSummary[]) =>
  (chapters.find((c) => c.open > 0) ?? chapters[0])?.chapter_id;

/**
 * The next chapter with open items: the first after this one by ordinal, else the first
 * before it, so "nothing left" is only said when it is true for the whole book.
 */
export function nextChapter(chapters: ChapterSummary[], chapterId: string) {
  const here = chapters.find((c) => String(c.chapter_id) === chapterId);
  const open = chapters
    .filter((c) => c.open > 0 && String(c.chapter_id) !== chapterId)
    .sort((a, b) => a.ordinal - b.ordinal);
  return open.find((c) => here === undefined || c.ordinal > here.ordinal) ?? open[0];
}
