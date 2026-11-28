import { useMemo, useState, type ReactNode } from "react";
import { cpToUtf16 } from "@/lib/offsets";
import {
  changeText, layoutMarks, resultText, stateLabel, type ReviewBlock, type Suggestion,
} from "@/lib/review";
import { cn } from "@/lib/utils";

interface Props {
  block: ReviewBlock;
  /** Whether a suggestion is in the current view; only those are focusable. Default: all. */
  isActive?: (s: Suggestion) => boolean;
  focusedId?: number | null;
  onSelect?: (id: number) => void;
}

const KIND_CLASS: Record<string, string> = {
  heading: "text-lg font-semibold",
  quote: "border-l-2 pl-4 italic",
  footnote: "text-sm",
};

/** Shown where a mark would otherwise be empty (an insertion point, a deletion). */
const caret = <span aria-hidden className="text-muted-foreground">⁁</span>;

function content(s: Suggestion, original: string): ReactNode {
  if (s.auto_applied) {
    return (
      <>
        {original && <del className="text-muted-foreground">{original}</del>}
        {s.replacement && <ins className="text-ai no-underline">{s.replacement}</ins>}
      </>
    );
  }
  const shown = s.status === "accepted" || s.status === "edited" ? resultText(s) : original;
  return shown || caret;
}

function tooltip(s: Suggestion): string {
  return [
    stateLabel(s),
    s.explanation,
    s.agreed && "both checkers agree",
    s.disputed && "the checkers disagree",
  ].filter(Boolean).join(" · ");
}

function markClass(s: Suggestion, active: boolean, focused: boolean): string {
  return cn(
    "underline-offset-4 transition-colors duration-150",
    s.status === "open" && active && "underline decoration-amber-500 decoration-2",
    s.status === "open" && !active && "underline decoration-dotted decoration-muted-foreground/60",
    s.status !== "open" && !s.auto_applied && "underline decoration-dotted decoration-foreground/25",
    active && "inline cursor-pointer rounded-sm text-left align-baseline hover:bg-accent focus-visible:outline-2 focus-visible:outline-ring",
    focused && "bg-amber-100 ring-2 ring-ring dark:bg-amber-900/40",
  );
}

function Mark({ s, original, active, focused, onSelect }: {
  s: Suggestion; original: string; active: boolean; focused: boolean; onSelect?: (id: number) => void;
}) {
  const shared = { "data-sid": s.id, title: tooltip(s), className: markClass(s, active, focused) };
  return (
    <>
      {active ? (
        <button
          type="button"
          {...shared}
          aria-label={`${stateLabel(s)}: ${changeText(s)}`}
          aria-current={focused ? "true" : undefined}
          onClick={() => onSelect?.(s.id)}
        >
          {content(s, original)}
        </button>
      ) : (
        <span {...shared}>{content(s, original)}</span>
      )}
      {s.disputed && s.status === "open" && (
        <span
          title={`Other fix: ${s.alternative ?? "none given"}`}
          className="ml-1 rounded bg-amber-100 px-1 align-super text-[0.65rem] text-amber-900 dark:bg-amber-900/40 dark:text-amber-100"
        >
          the checkers disagree
        </span>
      )}
    </>
  );
}

export default function Paragraph({ block, isActive = () => true, focusedId = null, onSelect }: Props) {
  const [showSource, setShowSource] = useState(false);
  const marks = useMemo(() => layoutMarks(block.suggestions), [block.suggestions]);
  const notes = block.suggestions.filter((s) => s.auto_applied && s.status !== "superseded");

  const text = block.original;
  const parts: ReactNode[] = [];
  let at = 0; // UTF-16 index into text
  for (const s of marks) {
    const from = cpToUtf16(text, s.start);
    const to = cpToUtf16(text, s.end);
    if (from > at) parts.push(text.slice(at, from));
    parts.push(
      <Mark
        key={s.id}
        s={s}
        original={text.slice(from, to)}
        active={isActive(s)}
        focused={s.id === focusedId}
        onSelect={onSelect}
      />,
    );
    at = to;
  }
  if (at < text.length) parts.push(text.slice(at));

  const body = (
    <div className={cn(block.source == null && "md:grid md:grid-cols-[minmax(0,1fr)_14rem] md:gap-6")}>
      <p className={cn("leading-8", KIND_CLASS[block.kind])}>{parts}</p>
      {notes.length > 0 && (
        <aside aria-label="AI notes" className="mt-1 space-y-1 border-l-2 border-ai/40 pl-3 text-xs md:mt-1.5">
          {notes.map((n) => (
            <p key={n.id} className="text-ai">
              <span className="font-semibold">AI</span> {changeText(n)}. {n.explanation}
            </p>
          ))}
        </aside>
      )}
    </div>
  );
  if (block.source == null) return body;

  // A translated block: the source paragraph beside the text from 1024 px up, behind a
  // toggle under it on narrower screens.
  // In the DOM the translated text comes first, so narrow screens read text, then toggle.
  // From lg the source column is ordered first: source left, translation right.
  return (
    <div className="flex flex-col lg:grid lg:grid-cols-2 lg:gap-6">
      {body}
      <div data-source-column className="order-last mt-2 lg:order-first lg:mt-0">
        <button
          type="button"
          className="text-xs text-muted-foreground underline underline-offset-2 lg:hidden"
          aria-expanded={showSource}
          onClick={() => setShowSource((v) => !v)}
        >
          {showSource ? "Hide original" : "Show original"}
        </button>
        <div
          aria-label="Source paragraph"
          role="note"
          className={cn(
            showSource ? "block" : "hidden",
            "mt-1 border-l-2 border-muted-foreground/30 pl-3 leading-8 text-muted-foreground",
            "lg:mt-0 lg:block",
            KIND_CLASS[block.kind],
          )}
        >
          {block.source}
        </div>
      </div>
    </div>
  );
}
