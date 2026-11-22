import { useState } from "react";
import { ChevronRightIcon } from "lucide-react";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { NATIVE_SELECT } from "@/components/NativeSelect";
import { QUOTE_PRESETS } from "@/lib/format";

export interface HouseStyleValue {
  /** null means "the language's default". */
  quote: string | null;
  spellings: string;
  ignore: string;
}

export const EMPTY_STYLE: HouseStyleValue = { quote: null, spellings: "", ignore: "" };

/** One rule per line: `canonical: variant1, variant2`. */
export function parseSpellings(
  text: string,
): { value: Record<string, string[]> } | { error: string } {
  const out: Record<string, string[]> = {};
  const lines = text.split("\n");
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i].trim();
    if (!line) continue;
    const at = line.indexOf(":");
    const canonical = at < 0 ? "" : line.slice(0, at).trim();
    const variants = at < 0 ? [] : line.slice(at + 1).split(",").map((s) => s.trim()).filter(Boolean);
    if (!canonical || variants.length === 0) {
      return { error: `Line ${i + 1}: write it as "canonical: variant1, variant2".` };
    }
    out[canonical] = [...(out[canonical] ?? []), ...variants];
  }
  return { value: out };
}

export function parseIgnore(text: string): string[] {
  return text.split(/[\n,]/).map((s) => s.trim()).filter(Boolean);
}

interface Props {
  value: HouseStyleValue;
  onChange: (v: HouseStyleValue) => void;
  defaultQuote: string;
  spellingsError: string | null;
}

export default function HouseStyleSection({ value, onChange, defaultQuote, spellingsError }: Props) {
  const [open, setOpen] = useState(false);
  return (
    <section>
      <button
        type="button"
        aria-expanded={open}
        aria-controls="house-style-panel"
        className="flex items-center gap-1 text-sm font-medium"
        onClick={() => setOpen(!open)}
      >
        <ChevronRightIcon className={"size-4 transition-transform " + (open ? "rotate-90" : "")} />
        House style
      </button>
      <div id="house-style-panel" hidden={!open} className="mt-3 space-y-4">
        <div className="space-y-2">
          <Label htmlFor="quote-style">Quote style</Label>
          <select
            id="quote-style"
            className={NATIVE_SELECT}
            value={value.quote ?? defaultQuote}
            onChange={(e) => onChange({ ...value, quote: e.target.value })}
          >
            {QUOTE_PRESETS.map((p) => (
              <option key={p.value} value={p.value}>{p.label}</option>
            ))}
          </select>
        </div>
        <div className="space-y-2">
          <Label htmlFor="house-spellings">House spellings</Label>
          <Textarea
            id="house-spellings"
            rows={4}
            placeholder="canonical: variant1, variant2"
            aria-describedby="house-spellings-help"
            aria-invalid={spellingsError ? true : undefined}
            value={value.spellings}
            onChange={(e) => onChange({ ...value, spellings: e.target.value })}
          />
          <p id="house-spellings-help" className="text-sm text-muted-foreground">
            One rule per line, for example: colour: color, colr
          </p>
          {spellingsError && <p role="alert" className="text-sm text-destructive">{spellingsError}</p>}
        </div>
        <div className="space-y-2">
          <Label htmlFor="ignore-words">Words to ignore</Label>
          <Textarea
            id="ignore-words"
            rows={2}
            placeholder="Separate with commas or new lines"
            value={value.ignore}
            onChange={(e) => onChange({ ...value, ignore: e.target.value })}
          />
        </div>
      </div>
    </section>
  );
}
