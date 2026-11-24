import { useEffect, useRef, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
  categoryLabel, canAccept, canEdit, canReject, resultText, RULE_LABELS, SOURCE_LABELS, stateLabel,
  type Suggestion,
} from "@/lib/review";
import { cn } from "@/lib/utils";

interface Props {
  s: Suggestion;
  /** The mark that took its place in the text; null when it is marked itself. */
  hiddenBy: Suggestion | null;
  /** The source paragraph on a translated book. */
  source?: string | null;
  editing: boolean;
  busy: boolean;
  canUndo: boolean;
  onAccept: () => void;
  onReject: () => void;
  onEdit: () => void;
  onUndo: () => void;
  onSave: (replacement: string) => void;
  onCancelEdit: () => void;
}

function Change({ s }: { s: Suggestion }) {
  const after = resultText(s);
  return (
    <p className="flex flex-wrap items-baseline gap-2 text-base">
      {s.original ? <span className="text-muted-foreground line-through">{s.original}</span> : <em>insert</em>}
      <span aria-hidden>→</span>
      <span className="sr-only">becomes</span>
      {after ? <span className={cn("font-medium", s.auto_applied && "text-ai")}>{after}</span> : <em>delete</em>}
    </p>
  );
}

function EditField({ initial, busy, onSave, onCancel }: {
  initial: string; busy: boolean; onSave: (t: string) => void; onCancel: () => void;
}) {
  const [text, setText] = useState(initial);
  const field = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    // Open with the caret after the text, ready to change its end.
    const el = field.current;
    el?.focus();
    el?.setSelectionRange(el.value.length, el.value.length);
  }, []);
  return (
    <form
      className="space-y-2"
      onSubmit={(e) => {
        e.preventDefault();
        onSave(text);
      }}
    >
      <Label htmlFor="review-edit">Replacement</Label>
      <Textarea
        id="review-edit"
        ref={field}
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Escape") {
            e.preventDefault();
            onCancel();
          } else if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
            e.preventDefault();
            onSave(text);
          }
        }}
      />
      <div className="flex gap-2">
        <Button type="submit" size="sm" disabled={busy}>Save edit</Button>
        <Button type="button" size="sm" variant="ghost" onClick={onCancel}>Cancel (Esc)</Button>
      </div>
    </form>
  );
}

/** Why a suggestion is not marked, naming what is shown there instead. */
function hiddenNote(by: Suggestion): string {
  const what =
    by.status === "accepted" || by.status === "edited"
      ? "a change already applied there"
      : by.status === "open"
        ? "another open suggestion marked there"
        : "a rejected suggestion shown there";
  return `Not marked in the text: it overlaps ${what}.`;
}

function Key({ k }: { k: string }) {
  return <span className="text-muted-foreground">({k})</span>;
}

/**
 * The focused suggestion's details and actions. It is sticky inside its paragraph's container:
 * while a long paragraph is on screen it stays pinned to the bottom of the screen, so what
 * `a` will accept is always in view, then it settles under the paragraph.
 */
export default function FocusCard(p: Props) {
  const { s } = p;
  return (
    <section
      aria-label="Focused suggestion"
      data-focus-card
      tabIndex={-1}
      className="sticky bottom-4 z-[5] mt-2 mb-2 space-y-3 rounded-lg border bg-card p-4 shadow-md outline-none md:mr-[15.5rem]"
    >
      <Change s={s} />
      {p.source != null && (
        <div aria-label="Original paragraph" className="rounded-md bg-muted/50 p-2 text-sm">
          <span className="font-medium">Original: </span>
          <span className="text-muted-foreground">{p.source}</span>
        </div>
      )}
      <div className="flex flex-wrap gap-1.5">
        <Badge variant="secondary">{categoryLabel(s.category)}</Badge>
        <Badge variant="outline">{s.severity}</Badge>
        <Badge variant="outline">{SOURCE_LABELS[s.source] ?? s.source}</Badge>
      </div>
      {s.explanation && (
        <p className="text-sm">
          <span className="font-medium">Why: </span>
          <span>{s.explanation}</span>
        </p>
      )}
      {s.agreed && <p className="text-sm text-green-700 dark:text-green-400">both checkers agree</p>}
      {s.disputed && (
        <p className="text-sm text-amber-800 dark:text-amber-300">
          <span>the checkers disagree</span>
          {s.alternative !== null && <span>{` · Other fix: “${s.alternative}”`}</span>}
        </p>
      )}
      {s.rule && <p className="text-sm">{RULE_LABELS[s.rule] ?? s.rule}</p>}
      <p className={cn("text-sm text-muted-foreground", s.auto_applied && "text-ai")}>{stateLabel(s)}</p>
      {p.hiddenBy && <p className="text-sm text-muted-foreground">{hiddenNote(p.hiddenBy)}</p>}
      {p.editing ? (
        <EditField initial={resultText(s)} busy={p.busy} onSave={p.onSave} onCancel={p.onCancelEdit} />
      ) : (
        <div className="flex flex-wrap gap-2">
          <Button size="sm" onClick={p.onAccept} disabled={p.busy || !canAccept(s)}>
            Accept <Key k="a" />
          </Button>
          <Button
            size="sm"
            variant="outline"
            onClick={p.onReject}
            disabled={p.busy || !canReject(s)}
            title={s.auto_applied ? "Reverts the AI's change" : undefined}
          >
            Reject <Key k="r" />
          </Button>
          <Button size="sm" variant="outline" onClick={p.onEdit} disabled={p.busy || !canEdit(s)}>
            Edit <Key k="e" />
          </Button>
          <Button size="sm" variant="ghost" onClick={p.onUndo} disabled={p.busy || !p.canUndo}>
            Undo <Key k="u" />
          </Button>
        </div>
      )}
    </section>
  );
}
