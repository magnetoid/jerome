import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { ApiError, apiFetch } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import DropZone from "@/components/DropZone";
import { NATIVE_SELECT } from "@/components/NativeSelect";
import HouseStyleSection, {
  EMPTY_STYLE, parseIgnore, parseSpellings, type HouseStyleValue,
} from "@/components/HouseStyleSection";
import { DEFAULT_QUOTES, VARIANTS } from "@/lib/format";
import { PROJECTS_KEY } from "@/hooks/useProjects";

const DEFAULT_VARIANT = "en-GB";

interface Created {
  id: number | string;
}

function detailOf(e: unknown): string {
  return e instanceof ApiError ? e.detail : "Something went wrong. Try again.";
}

export default function NewProjectPage() {
  const navigate = useNavigate();
  const client = useQueryClient();
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [titleEdited, setTitleEdited] = useState(false);
  const [variant, setVariant] = useState(DEFAULT_VARIANT);
  const [autoApply, setAutoApply] = useState(true);
  const [style, setStyle] = useState<HouseStyleValue>(EMPTY_STYLE);
  const [fileError, setFileError] = useState<string | null>(null);

  const defaultQuote = DEFAULT_QUOTES[variant];
  const spellings = parseSpellings(style.spellings);
  const spellingsError = "error" in spellings ? spellings.error : null;

  /** Only fields that differ from their defaults. */
  function styleChanges(): Record<string, unknown> {
    const body: Record<string, unknown> = {};
    if (style.quote !== null && style.quote !== defaultQuote) body.quote_style = style.quote;
    if ("value" in spellings && Object.keys(spellings.value).length > 0) {
      body.house_spellings = spellings.value;
    }
    const ignore = parseIgnore(style.ignore);
    if (ignore.length > 0) body.ignore_words = ignore;
    return body;
  }

  const create = useMutation({
    mutationFn: async () => {
      const form = new FormData();
      form.append("file", file!);
      form.append("variant", variant);
      if (title.trim()) form.append("title", title.trim());
      form.append("auto_apply", autoApply ? "true" : "false");
      const book = await apiFetch<Created>("/v1/books", { method: "POST", body: form });
      const changes = styleChanges();
      let styleError: string | null = null;
      if (Object.keys(changes).length > 0) {
        try {
          await apiFetch(`/v1/books/${book.id}/style-sheet`, { method: "PUT", json: changes });
        } catch (e) {
          styleError = detailOf(e);
        }
      }
      return { id: book.id, styleError };
    },
    onSuccess: ({ id, styleError }) => {
      void client.invalidateQueries({ queryKey: PROJECTS_KEY });
      if (styleError) {
        toast.error(`Project created, but the house style wasn't saved: ${styleError}`);
      }
      navigate(`/projects/${id}`);
    },
  });

  function pick(f: File) {
    setFileError(null);
    setFile(f);
    if (!titleEdited) setTitle(f.name.replace(/\.[^.]*$/, ""));
  }

  const error = fileError ?? (create.isError ? detailOf(create.error) : null);

  return (
    <main className="mx-auto max-w-2xl p-4 sm:p-8">
      <h1 className="mb-6 text-2xl font-semibold">New project</h1>
      <form
        className="space-y-6"
        onSubmit={(e) => {
          e.preventDefault();
          if (file && !spellingsError && !create.isPending) create.mutate();
        }}
      >
        <DropZone file={file} onFile={pick} onReject={setFileError} />

        <div className="space-y-2">
          <Label htmlFor="title">Title</Label>
          <Input
            id="title"
            value={title}
            onChange={(e) => {
              setTitle(e.target.value);
              setTitleEdited(true);
            }}
          />
        </div>

        <div className="space-y-2">
          <Label htmlFor="variant">Language</Label>
          <select
            id="variant"
            className={NATIVE_SELECT}
            value={variant}
            onChange={(e) => setVariant(e.target.value)}
          >
            {VARIANTS.map((v) => (
              <option key={v.value} value={v.value}>{v.label}</option>
            ))}
          </select>
        </div>

        <div className="flex items-start gap-2">
          <Checkbox
            id="auto-apply"
            checked={autoApply}
            onCheckedChange={(c) => setAutoApply(c === true)}
          />
          <Label htmlFor="auto-apply" className="font-normal leading-snug">
            Let the AI apply clear-cut fixes (high-severity typos and punctuation, and house-style rules)
          </Label>
        </div>

        <HouseStyleSection
          value={style}
          onChange={setStyle}
          defaultQuote={defaultQuote}
          spellingsError={spellingsError}
        />

        {error && <p role="alert" className="text-sm text-destructive">{error}</p>}

        <Button type="submit" disabled={!file || create.isPending || spellingsError !== null}>
          {create.isPending ? "Creating…" : "Create project"}
        </Button>
      </form>
    </main>
  );
}
