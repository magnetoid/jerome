import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { toast } from "sonner";
import { apiFetch } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { NATIVE_SELECT } from "@/components/NativeSelect";
import HouseStyleSection, { parseIgnore, parseSpellings, type HouseStyleValue } from "@/components/HouseStyleSection";
import { DEFAULT_QUOTES } from "@/lib/format";
import { detailOf } from "@/lib/jobs";

interface StyleSheet {
  variant: string;
  quote_style: string | null;
  house_spellings: Record<string, string[]>;
  ignore_words: string[];
}

interface AutoApply {
  enabled: boolean;
  categories: string[];
  min_severity: "low" | "medium" | "high";
  house_rules: boolean;
}

const CATEGORIES = [
  { value: "typo", label: "Typos" },
  { value: "punctuation", label: "Punctuation" },
  { value: "grammar", label: "Grammar" },
  { value: "consistency", label: "Consistency" },
  { value: "style", label: "Style" },
];

function StyleForm({ bookId, sheet }: { bookId: string; sheet: StyleSheet }) {
  const defaultQuote = DEFAULT_QUOTES[sheet.variant] ?? "“”‘’";
  const [value, setValue] = useState<HouseStyleValue>({
    quote: sheet.quote_style,
    spellings: Object.entries(sheet.house_spellings).map(([k, v]) => `${k}: ${v.join(", ")}`).join("\n"),
    ignore: sheet.ignore_words.join(", "),
  });
  const spellings = parseSpellings(value.spellings);
  const spellingsError = "error" in spellings ? spellings.error : null;

  const save = useMutation({
    mutationFn: () =>
      apiFetch(`/v1/books/${bookId}/style-sheet`, {
        method: "PUT",
        json: {
          quote_style: value.quote ?? defaultQuote,
          house_spellings: "value" in spellings ? spellings.value : {},
          ignore_words: parseIgnore(value.ignore),
        },
      }),
    onSuccess: () => toast.success("House style saved"),
  });

  return (
    <div className="space-y-4">
      <HouseStyleSection value={value} onChange={setValue} defaultQuote={defaultQuote} spellingsError={spellingsError} />
      {save.isError && <p role="alert" className="text-sm text-destructive">{detailOf(save.error)}</p>}
      <Button type="button" disabled={spellingsError !== null || save.isPending} onClick={() => save.mutate()}>
        Save house style
      </Button>
    </div>
  );
}

function AutoApplyForm({ bookId, initial }: { bookId: string; initial: AutoApply }) {
  const [policy, setPolicy] = useState<AutoApply>(initial);
  const save = useMutation({
    mutationFn: () =>
      apiFetch<AutoApply>(`/v1/books/${bookId}/auto-apply`, {
        method: "PUT",
        json: { ...policy, categories: [...policy.categories].sort() },
      }),
    onSuccess: () => toast.success("Auto-apply saved"),
  });
  const toggleCat = (c: string, on: boolean) =>
    setPolicy({
      ...policy,
      categories: on ? [...policy.categories, c] : policy.categories.filter((x) => x !== c),
    });

  return (
    <div className="space-y-4">
      <div className="flex items-start gap-2">
        <Checkbox
          id="aa-enabled"
          checked={policy.enabled}
          onCheckedChange={(c) => setPolicy({ ...policy, enabled: c === true })}
        />
        <Label htmlFor="aa-enabled" className="font-normal leading-snug">Let the AI apply clear-cut fixes</Label>
      </div>
      <fieldset disabled={!policy.enabled} className="space-y-3">
        <legend className="mb-1 text-sm font-medium">Which kinds of fix</legend>
        {CATEGORIES.map((c) => (
          <div key={c.value} className="flex items-center gap-2">
            <Checkbox
              id={`aa-${c.value}`}
              checked={policy.categories.includes(c.value)}
              onCheckedChange={(v) => toggleCat(c.value, v === true)}
            />
            <Label htmlFor={`aa-${c.value}`} className="font-normal">{c.label}</Label>
          </div>
        ))}
        <div className="max-w-xs space-y-2">
          <Label htmlFor="aa-severity">Minimum severity</Label>
          <select
            id="aa-severity"
            className={NATIVE_SELECT}
            value={policy.min_severity}
            onChange={(e) => setPolicy({ ...policy, min_severity: e.target.value as AutoApply["min_severity"] })}
          >
            <option value="high">High</option>
            <option value="medium">Medium</option>
            <option value="low">Low</option>
          </select>
        </div>
        <div className="flex items-center gap-2">
          <Checkbox
            id="aa-house"
            checked={policy.house_rules}
            onCheckedChange={(c) => setPolicy({ ...policy, house_rules: c === true })}
          />
          <Label htmlFor="aa-house" className="font-normal">Also apply house style rules</Label>
        </div>
      </fieldset>
      {save.isError && <p role="alert" className="text-sm text-destructive">{detailOf(save.error)}</p>}
      <Button type="button" disabled={save.isPending} onClick={() => save.mutate()}>Save auto-apply</Button>
    </div>
  );
}

export default function SettingsTab({ bookId }: { bookId: string }) {
  const sheet = useQuery({
    queryKey: ["style-sheet", bookId],
    queryFn: () => apiFetch<StyleSheet>(`/v1/books/${bookId}/style-sheet`),
    staleTime: Infinity,
  });
  const auto = useQuery({
    queryKey: ["auto-apply", bookId],
    queryFn: () => apiFetch<AutoApply>(`/v1/books/${bookId}/auto-apply`),
    staleTime: Infinity,
  });

  return (
    <div className="space-y-8">
      <p className="text-sm text-muted-foreground">Changes take effect from the next run.</p>
      <section className="space-y-3">
        <h2 className="text-lg font-medium">House style</h2>
        {sheet.isError && <p role="alert" className="text-sm text-destructive">{detailOf(sheet.error)}</p>}
        {sheet.data && <StyleForm bookId={bookId} sheet={sheet.data} />}
      </section>
      <section className="space-y-3">
        <h2 className="text-lg font-medium">Auto-apply</h2>
        {auto.isError && <p role="alert" className="text-sm text-destructive">{detailOf(auto.error)}</p>}
        {auto.data && <AutoApplyForm bookId={bookId} initial={auto.data} />}
      </section>
    </div>
  );
}
