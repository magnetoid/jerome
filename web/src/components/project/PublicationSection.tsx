import { useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { NATIVE_SELECT } from "@/components/NativeSelect";
import { detailOf } from "@/lib/jobs";
import {
  TRIM_SIZES, useDeleteCover, usePublication, useSavePublication, useUploadCover,
  type Publication, type PublicationValues,
} from "@/lib/publication";
import Loading from "@/components/Loading";

/** Text fields of the form, with their labels; an empty field is sent as null (cleared). */
const TEXT_FIELDS = [
  ["translator", "Translator"],
  ["publisher", "Publisher"],
  ["isbn_epub", "ISBN (EPUB)"],
  ["isbn_pdf", "ISBN (PDF)"],
  ["rights", "Rights line"],
] as const;

type TextField = (typeof TEXT_FIELDS)[number][0];

/** The book's publication details and cover, which the finished EPUB and PDF use. */
export default function PublicationSection({ bookId }: { bookId: string }) {
  const publication = usePublication(bookId);
  return (
    <section className="space-y-3">
      <h2 className="text-lg font-medium">Publication</h2>
      {publication.isPending && <Loading />}
      {publication.isError && (
        <p role="alert" className="text-sm text-destructive">{detailOf(publication.error)}</p>
      )}
      {publication.data && (
        <>
          <DetailsForm bookId={bookId} initial={publication.data} />
          <Cover bookId={bookId} hasCover={publication.data.has_cover} />
        </>
      )}
    </section>
  );
}

function DetailsForm({ bookId, initial }: { bookId: string; initial: Publication }) {
  const save = useSavePublication(bookId);
  const [authors, setAuthors] = useState(initial.authors.join("\n"));
  const [text, setText] = useState<Record<TextField, string>>(() => ({
    translator: initial.translator ?? "",
    publisher: initial.publisher ?? "",
    isbn_epub: initial.isbn_epub ?? "",
    isbn_pdf: initial.isbn_pdf ?? "",
    rights: initial.rights ?? "",
  }));
  const [year, setYear] = useState(initial.year != null ? String(initial.year) : "");
  const [description, setDescription] = useState(initial.description ?? "");
  const [trim, setTrim] = useState(initial.trim_size);

  function onSave() {
    const values: PublicationValues = {
      authors: authors.split("\n").map((a) => a.trim()).filter(Boolean),
      year: year.trim() === "" ? null : Number(year),
      description: description.trim() === "" ? null : description,
      trim_size: trim,
    };
    for (const [key] of TEXT_FIELDS) values[key] = text[key].trim() === "" ? null : text[key].trim();
    save.mutate(values);
  }

  return (
    <div className="space-y-3">
      <div className="grid gap-3 sm:grid-cols-2">
        <div className="grid gap-2 sm:col-span-2">
          <Label htmlFor="pub-authors">Authors (one per line)</Label>
          <Textarea id="pub-authors" rows={2} value={authors} onChange={(e) => setAuthors(e.target.value)} />
        </div>
        {TEXT_FIELDS.map(([key, label]) => (
          <div key={key} className="grid gap-2">
            <Label htmlFor={`pub-${key}`}>{label}</Label>
            <Input
              id={`pub-${key}`}
              value={text[key]}
              onChange={(e) => setText({ ...text, [key]: e.target.value })}
            />
          </div>
        ))}
        <div className="grid gap-2">
          <Label htmlFor="pub-year">Year</Label>
          <Input id="pub-year" inputMode="numeric" value={year} onChange={(e) => setYear(e.target.value)} />
        </div>
        <div className="grid gap-2">
          <Label htmlFor="pub-trim">PDF page size</Label>
          <select id="pub-trim" className={NATIVE_SELECT} value={trim} onChange={(e) => setTrim(e.target.value)}>
            {TRIM_SIZES.map((t) => (
              <option key={t.value} value={t.value}>{t.label}</option>
            ))}
          </select>
        </div>
        <div className="grid gap-2 sm:col-span-2">
          <Label htmlFor="pub-description">Description</Label>
          <Textarea
            id="pub-description"
            rows={3}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
        </div>
      </div>
      <div className="flex items-center gap-3">
        <Button onClick={onSave} disabled={save.isPending}>Save details</Button>
        {save.isSuccess && !save.isPending && <span className="text-sm text-muted-foreground">Saved.</span>}
      </div>
      {save.isError && <p role="alert" className="text-sm text-destructive">{detailOf(save.error)}</p>}
    </div>
  );
}

function Cover({ bookId, hasCover }: { bookId: string; hasCover: boolean }) {
  const upload = useUploadCover(bookId);
  const remove = useDeleteCover(bookId);
  const input = useRef<HTMLInputElement>(null);
  // A new upload keeps the same URL: a counter makes the browser load it again.
  const [version, setVersion] = useState(0);
  const error = upload.error ?? remove.error;

  function onFile(file: File | undefined) {
    if (!file) return;
    upload.mutate(file, { onSuccess: () => setVersion((v) => v + 1) });
    if (input.current) input.current.value = "";
  }

  return (
    <div className="space-y-2">
      <h3 className="font-medium">Cover</h3>
      {hasCover ? (
        <img
          src={`/v1/books/${bookId}/cover?v=${version}`}
          alt="Cover"
          className="h-48 w-auto rounded border"
        />
      ) : (
        <p className="text-sm text-muted-foreground">
          Shops need a cover: a JPEG or PNG at least 1400 px on its long side.
        </p>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <Label htmlFor="pub-cover" className="sr-only">Cover image</Label>
        <Input
          id="pub-cover"
          ref={input}
          type="file"
          accept="image/jpeg,image/png"
          className="max-w-xs"
          disabled={upload.isPending}
          onChange={(e) => onFile(e.target.files?.[0])}
        />
        {hasCover && (
          <Button variant="outline" onClick={() => remove.mutate()} disabled={remove.isPending}>
            Remove cover
          </Button>
        )}
      </div>
      {error && <p role="alert" className="text-sm text-destructive">{detailOf(error)}</p>}
    </div>
  );
}
