import type { ReactNode } from "react";
import { NATIVE_SELECT } from "@/components/NativeSelect";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import type { ChapterSummary } from "@/lib/jobs";
import { STATES, STATE_LABELS, type PreviewState, type Prefs } from "@/lib/preview";
import { cn } from "@/lib/utils";

function Group({ label, children, role }: { label: string; children: ReactNode; role?: string }) {
  return (
    <div className="grid gap-1">
      <span className="text-xs text-muted-foreground">{label}</span>
      <div role={role ?? "group"} aria-label={label} className="flex rounded-md border p-0.5">
        {children}
      </div>
    </div>
  );
}

function Toggle({ on, role, disabled, onClick, children }: {
  on: boolean; role: "radio" | "checkbox"; disabled?: boolean; onClick: () => void; children: ReactNode;
}) {
  return (
    <button
      type="button"
      role={role}
      aria-checked={on}
      disabled={disabled}
      onClick={onClick}
      className={cn(
        "h-7 rounded px-2.5 text-sm transition-colors disabled:cursor-not-allowed",
        on ? "bg-secondary font-medium text-secondary-foreground" : "text-muted-foreground hover:text-foreground",
        disabled && !on && "opacity-50",
      )}
    >
      {children}
    </button>
  );
}

function Choice<T extends string>({ label, value, options, onChange }: {
  label: string; value: T; options: [T, string][]; onChange: (v: T) => void;
}) {
  return (
    <Group label={label} role="radiogroup">
      {options.map(([v, text]) => (
        <Toggle key={v} role="radio" on={value === v} onClick={() => onChange(v)}>{text}</Toggle>
      ))}
    </Group>
  );
}

/** The preview's controls: chapter, view, layout, the states shown and Show changes. */
export default function PreviewToolbar({ chapters, chapterId, onChapter, prefs, onPrefs, translated }: {
  chapters: ChapterSummary[];
  chapterId: string;
  onChapter: (id: string) => void;
  prefs: Prefs;
  onPrefs: (p: Prefs) => void;
  translated: boolean;
}) {
  const offered = STATES.filter((s) => translated || s !== "translation");
  const toggle = (s: PreviewState) =>
    onPrefs({ ...prefs, states: prefs.states.includes(s) ? prefs.states.filter((x) => x !== s) : [...prefs.states, s] });
  return (
    <div className="sticky top-0 z-20 -mx-4 border-b bg-background/95 px-4 py-3 backdrop-blur sm:-mx-6 sm:px-6">
      <div className="flex flex-wrap items-end gap-3">
        <div className="grid gap-1">
          <Label htmlFor="preview-chapter" className="text-xs text-muted-foreground">Chapter</Label>
          <select
            id="preview-chapter"
            className={cn(NATIVE_SELECT, "w-56 max-w-full")}
            value={chapterId}
            onChange={(e) => onChapter(e.target.value)}
          >
            {chapters.map((c) => (
              <option key={c.chapter_id} value={String(c.chapter_id)}>{`${c.ordinal}. ${c.title}`}</option>
            ))}
          </select>
        </div>
        <Choice label="View" value={prefs.view} options={[["text", "Text"], ["book", "Book"]]} onChange={(view) => onPrefs({ ...prefs, view })} />
        <Choice label="Layout" value={prefs.layout} options={[["single", "Single"], ["side", "Side by side"]]} onChange={(layout) => onPrefs({ ...prefs, layout })} />
        {prefs.layout === "single" ? (
          <Choice
            label="State"
            value={prefs.states[0]}
            options={offered.map((s) => [s, STATE_LABELS[s]])}
            onChange={(s) => onPrefs({ ...prefs, states: [s] })}
          />
        ) : (
          <Group label="States">
            {offered.map((s) => {
              const on = prefs.states.includes(s);
              return (
                <Toggle key={s} role="checkbox" on={on} disabled={on && prefs.states.length <= 2} onClick={() => toggle(s)}>
                  {STATE_LABELS[s]}
                </Toggle>
              );
            })}
          </Group>
        )}
        <div className="flex h-9 items-center gap-2">
          <Checkbox
            id="preview-changes"
            checked={prefs.changes}
            onCheckedChange={(v) => onPrefs({ ...prefs, changes: v === true })}
          />
          <Label htmlFor="preview-changes">Show changes</Label>
        </div>
      </div>
    </div>
  );
}
