import type { ReactNode } from "react";
import { cpToUtf16 } from "@/lib/offsets";
import type { StateText } from "@/lib/preview";
import { cn } from "@/lib/utils";

const PLACEHOLDER = "￼";

/** Text with each picture (U+FFFC) shown as a small "Picture" tag. */
function plain(text: string, key: string): ReactNode[] {
  return text.split(PLACEHOLDER).flatMap((part, i) =>
    i === 0
      ? [part]
      : [
          <span key={`${key}-p${i}`} className="mx-0.5 rounded border px-1 text-xs text-muted-foreground">
            Picture
          </span>,
          part,
        ],
  );
}

const KIND_CLASS: Record<string, string> = {
  heading: "font-semibold text-base",
  quote: "border-l-2 pl-3 italic",
  footnote: "text-xs text-muted-foreground",
  list_item: "pl-4 before:-ml-3 before:mr-1.5 before:content-['•']",
};

/**
 * One paragraph in the Text view. With `changes` on, what proofreading removed is struck
 * through and what it put in is underlined, removed first.
 */
export default function TextCell({ text, kind, changes }: { text: StateText; kind: string; changes: boolean }) {
  const parts: ReactNode[] = [];
  let at = 0;
  if (changes) {
    (text.changes ?? []).forEach((cpSpan, i) => {
      // The API counts code points; JavaScript slices UTF-16 units.
      const span = { ...cpSpan, start: cpToUtf16(text.text, cpSpan.start), end: cpToUtf16(text.text, cpSpan.end) };
      parts.push(...plain(text.text.slice(at, span.start), `t${i}`));
      if (span.removed) {
        parts.push(
          <del key={`d${i}`} className="bg-red-500/10 text-red-700 decoration-red-700/60 dark:text-red-400">
            {plain(span.removed, `d${i}`)}
          </del>,
        );
      }
      if (span.end > span.start) {
        parts.push(
          <ins key={`i${i}`} className="bg-emerald-500/10 text-emerald-800 decoration-emerald-700/60 dark:text-emerald-300">
            {plain(text.text.slice(span.start, span.end), `i${i}`)}
          </ins>,
        );
      }
      at = span.end;
    });
  }
  parts.push(...plain(text.text.slice(at), "end"));
  return <p className={cn("whitespace-pre-wrap text-sm leading-relaxed", KIND_CLASS[kind])}>{parts}</p>;
}
