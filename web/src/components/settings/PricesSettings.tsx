import { useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { SettingFrame, useSettingActions, type SettingRow } from "./shared";

const FIELDS = ["input", "output", "cache_read", "cache_write"] as const;
type Field = (typeof FIELDS)[number];
type Entry = Record<Field, number>;
type Draft = { name: string } & Record<Field, string>;

function toDrafts(value: unknown): Draft[] {
  const map = (value ?? {}) as Record<string, Entry>;
  return Object.entries(map).map(([name, e]) => ({
    name,
    input: String(e.input),
    output: String(e.output),
    cache_read: String(e.cache_read),
    cache_write: String(e.cache_write),
  }));
}

export default function PricesSettings({ row }: { row: SettingRow }) {
  const { save, reset } = useSettingActions();
  const [rows, setRows] = useState<Draft[]>(() => toDrafts(row.value));

  const edit = (i: number, patch: Partial<Draft>) =>
    setRows((rs) => rs.map((r, j) => (j === i ? { ...r, ...patch } : r)));

  function onSave() {
    const value: Record<string, Entry> = {};
    for (const r of rows) {
      const name = r.name.trim();
      if (!name) return void toast.error("Every price row needs a model name");
      if (name in value) return void toast.error(`${name} is listed twice`);
      const entry = {} as Entry;
      for (const f of FIELDS) {
        const n = Number(r[f]);
        if (r[f].trim() === "" || !Number.isFinite(n)) {
          return void toast.error(`${name}: ${f} must be a number`);
        }
        entry[f] = n;
      }
      value[name] = entry;
    }
    save.mutate({ key: row.key, value });
  }

  return (
    <SettingFrame
      label="Prices"
      row={row}
      saveLabel="Save prices"
      busy={save.isPending || reset.isPending}
      onSave={onSave}
      onReset={() => reset.mutate(row.key)}
    >
      <p className="text-sm text-muted-foreground">
        Dollars per million tokens. A model without a price here or built in cannot be
        estimated. For DeepL (model name <code>deepl</code>), put the price per million
        characters in input and 0 in the others.
      </p>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Model</TableHead>
            {FIELDS.map((f) => (
              <TableHead key={f}>{f}</TableHead>
            ))}
            <TableHead className="text-right">
              <span className="sr-only">Remove</span>
            </TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((r, i) => (
            <TableRow key={i}>
              <TableCell>
                <Input
                  aria-label={`Model name, row ${i + 1}`}
                  value={r.name}
                  onChange={(e) => edit(i, { name: e.target.value })}
                />
              </TableCell>
              {FIELDS.map((f) => (
                <TableCell key={f}>
                  <Input
                    type="number"
                    step="any"
                    aria-label={`${r.name || `row ${i + 1}`} ${f}`}
                    value={r[f]}
                    onChange={(e) => edit(i, { [f]: e.target.value })}
                  />
                </TableCell>
              ))}
              <TableCell className="text-right">
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  aria-label={`Remove ${r.name || `row ${i + 1}`}`}
                  onClick={() => setRows((rs) => rs.filter((_, j) => j !== i))}
                >
                  Remove
                </Button>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      <div>
        <Button
          type="button"
          size="sm"
          variant="outline"
          onClick={() =>
            setRows((rs) => [...rs, { name: "", input: "0", output: "0", cache_read: "0", cache_write: "0" }])
          }
        >
          Add model
        </Button>
      </div>
    </SettingFrame>
  );
}
