import { useState } from "react";
import { toast } from "sonner";
import { Input } from "@/components/ui/input";
import { SettingFrame, useSettingActions, type SettingRow } from "./shared";

type Props = { row: SettingRow; label: string };

/** A whole-number or decimal setting, sent as a number. */
export function NumberSetting({ row, label }: Props) {
  const { save, reset } = useSettingActions();
  const [text, setText] = useState(String(row.value ?? ""));
  function onSave() {
    const n = Number(text);
    if (text.trim() === "" || !Number.isFinite(n)) {
      toast.error(`${label} must be a number`);
      return;
    }
    save.mutate({ key: row.key, value: n });
  }
  return (
    <SettingFrame
      label={label}
      row={row}
      busy={save.isPending || reset.isPending}
      onSave={onSave}
      onReset={() => reset.mutate(row.key)}
    >
      <Input
        type="number"
        aria-label={label}
        value={text}
        onChange={(e) => setText(e.target.value)}
      />
    </SettingFrame>
  );
}

/** A URL setting; empty saves null (the setting is off). */
export function UrlSetting({ row, label }: Props) {
  const { save, reset } = useSettingActions();
  const [text, setText] = useState(typeof row.value === "string" ? row.value : "");
  return (
    <SettingFrame
      label={label}
      row={row}
      busy={save.isPending || reset.isPending}
      onSave={() => save.mutate({ key: row.key, value: text.trim() === "" ? null : text.trim() })}
      onReset={() => reset.mutate(row.key)}
    >
      <Input
        type="url"
        aria-label={label}
        placeholder="https://example.com"
        value={text}
        onChange={(e) => setText(e.target.value)}
      />
    </SettingFrame>
  );
}
