import { useEffect, useRef, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
  AI_VERDICT_COLOURS, AI_VERDICT_LABELS, categoryLabel, canAccept, canAskAi, canEdit, canReject, canRemember, canUseAiFix,
  isDecided, optionsOf, resultText,
  RULE_LABELS, SOURCE_LABELS, stateLabel, type Suggestion,
} from "@/lib/review";
import { formatDateTime } from "@/lib/format";
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
  /** `u` puts this very mark back to open (the button reads Revert), not the last decision. */
  reverts?: boolean;
  onAccept: () => void;
  onReject: () => void;
  onEdit: () => void;
  onUndo: () => void;
  onSave: (replacement: string) => void;
  onCancelEdit: () => void;
  /** Asking the AI about this LanguageTool mark is under way. */
  asking: boolean;
  onAskAi: () => void;
  /** Accept the mark with the AI's fix. */
  onUseAiFix: () => void;
  /** Accept the mark with one of LanguageTool's options. */
  onUseOption: (text: string) => void;
  /** Learn the word for all books and reject its open marks here. */
  onRemember: () => void;
  remembering?: boolean;
}

/** Where the AI's fix comes from: which of LanguageTool's options, or the AI itself. */
function fixSource(s: Suggestion, fix: string) {
  const n = optionsOf(s).indexOf(fix);
  return n >= 0 ? `LanguageTool option ${n + 1}` : "the AI's own";
}

/** The AI's rating of a LanguageTool mark, once asked for: how sure, its fix, and why. */
function AiVerdict({ s }: { s: Suggestion }) {
  if (s.ai_verdict === null) return null;
  const fix = s.ai_verdict !== "not_an_error" ? s.ai_replacement : null;
  return (
    <p aria-label="AI verdict" className="text-sm">
      <span className={cn("font-medium", AI_VERDICT_COLOURS[s.ai_verdict])}>{AI_VERDICT_LABELS[s.ai_verdict]}</span>
      {fix !== null && <span>{` · fix${fix ? ` “${fix}”` : ": delete"} (${fixSource(s, fix)})`}</span>}
      {s.ai_reason && <span>{` · ${s.ai_reason}`}</span>}
    </p>
  );
}

/** LanguageTool's options for an open mark, when it offered more than one. */
function Options({ s, busy, onUse }: { s: Suggestion; busy: boolean; onUse: (text: string) => void }) {
  const options = optionsOf(s);
  if (s.status !== "open" || options.length < 2) return null;
  const pick = s.ai_verdict !== "not_an_error" ? s.ai_replacement : null;
  return (
    <div role="group" aria-label="LanguageTool options" className="flex flex-wrap items-center gap-1.5 text-sm">
      <span className="font-medium">Options:</span>
      {options.map((o, i) => (
        <Button
          key={`${i}-${o}`}
          size="sm"
          variant="outline"
          className={cn("h-7 px-2", o === pick && "border-ai")}
          disabled={busy}
          onClick={() => onUse(o)}
        >
          {`${i + 1}. ${o || "delete"}`}
          {o === pick && <span className="text-ai">{" · AI's pick"}</span>}
        </Button>
      ))}
    </div>
  );
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
  // The main button follows the AI's rating: Reject when it is certainly fine, the AI's fix
  // when it is certainly an error with another fix, and none when it may be an error.
  const open = s.status === "open";
  const rejectFirst = open && s.ai_verdict === "not_an_error";
  const fixFirst = open && s.ai_verdict === "error" && canUseAiFix(s);
  const acceptFirst = !rejectFirst && !fixFirst && !(open && s.ai_verdict === "maybe_error");
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
      <AiVerdict s={s} />
      <Options s={s} busy={p.busy} onUse={p.onUseOption} />
      <p className={cn("text-sm text-muted-foreground", s.auto_applied && "text-ai")}>
        {isDecided(s) && s.decided_at ? `${stateLabel(s)} · ${formatDateTime(s.decided_at)}` : stateLabel(s)}
      </p>
      {p.hiddenBy && <p className="text-sm text-muted-foreground">{hiddenNote(p.hiddenBy)}</p>}
      {p.editing ? (
        <EditField initial={resultText(s)} busy={p.busy} onSave={p.onSave} onCancel={p.onCancelEdit} />
      ) : (
        <div className="flex flex-wrap gap-2">
          <Button
            size="sm"
            variant={acceptFirst ? "default" : "outline"}
            onClick={p.onAccept}
            disabled={p.busy || !canAccept(s)}
          >
            Accept <Key k="a" />
          </Button>
          <Button
            size="sm"
            variant={rejectFirst ? "default" : "outline"}
            onClick={p.onReject}
            disabled={p.busy || !canReject(s)}
            title={s.auto_applied ? "Reverts the AI's change" : undefined}
          >
            Reject <Key k="r" />
          </Button>
          <Button size="sm" variant="outline" onClick={p.onEdit} disabled={p.busy || !canEdit(s)}>
            Edit <Key k="e" />
          </Button>
          <Button
            size="sm"
            variant="ghost"
            onClick={p.onUndo}
            disabled={p.busy || !p.canUndo}
            title={p.reverts ? "Puts this mark back to open" : "Undoes your last decision in this chapter"}
          >
            {p.reverts ? "Revert" : "Undo"} <Key k="u" />
          </Button>
          {canUseAiFix(s) && (
            <Button
              size="sm"
              variant={fixFirst ? "default" : "outline"}
              className={cn(!fixFirst && "border-ai text-ai")}
              onClick={p.onUseAiFix}
              disabled={p.busy}
            >
              Use AI fix
            </Button>
          )}
          {canRemember(s) && (
            <Button
              size="sm"
              variant="outline"
              onClick={p.onRemember}
              disabled={p.busy || p.remembering}
              title={`LanguageTool stops marking “${s.original}” in every book in this language. Rejects this mark and its other open marks in this book (for a rule other than spelling, under the same rule).`}
            >
              Remember for all books
            </Button>
          )}
          {canAskAi(s) && (
            <Button
              size="sm"
              variant="outline"
              onClick={p.onAskAi}
              disabled={p.busy || p.asking}
              title="Asks the AI whether LanguageTool is right here. A paid model call."
            >
              {p.asking ? "Asking AI…" : "Ask AI"}
            </Button>
          )}
        </div>
      )}
    </section>
  );
}
