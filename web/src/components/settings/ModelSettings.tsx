import { useState } from "react";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NATIVE_SELECT } from "@/components/NativeSelect";
import { SettingFrame, useSettingActions, type SettingRow } from "./shared";

type Ai = { provider?: string; model?: string; effort?: string; base_url?: string };
const EFFORTS = ["low", "medium", "high", "xhigh", "max"];

export default function ModelSettings({
  row,
  label,
  idPrefix,
}: {
  row: SettingRow;
  label: string;
  idPrefix: string;
}) {
  const { save, reset } = useSettingActions();
  const v = (row.value ?? {}) as Ai;
  const [provider, setProvider] = useState(v.provider ?? "anthropic");
  const [model, setModel] = useState(v.model ?? "");
  const [effort, setEffort] = useState(v.effort ?? "");
  const [baseUrl, setBaseUrl] = useState(v.base_url ?? "");

  function onSave() {
    const value: Ai = { provider, model: model.trim() };
    if (effort) value.effort = effort;
    if (provider === "openai_compat") value.base_url = baseUrl.trim();
    save.mutate({ key: row.key, value });
  }

  return (
    <SettingFrame
      label={label}
      row={row}
      busy={save.isPending || reset.isPending}
      onSave={onSave}
      onReset={() => reset.mutate(row.key)}
    >
      <div className="grid gap-3 sm:grid-cols-2">
        <div className="grid gap-2">
          <Label htmlFor={`${idPrefix}-provider`}>Provider</Label>
          <select
            id={`${idPrefix}-provider`}
            className={NATIVE_SELECT}
            value={provider}
            onChange={(e) => setProvider(e.target.value)}
          >
            <option value="anthropic">anthropic</option>
            <option value="openai_compat">openai_compat</option>
          </select>
        </div>
        <div className="grid gap-2">
          <Label htmlFor={`${idPrefix}-model`}>Model</Label>
          <Input id={`${idPrefix}-model`} value={model} onChange={(e) => setModel(e.target.value)} />
        </div>
        <div className="grid gap-2">
          <Label htmlFor={`${idPrefix}-effort`}>Effort</Label>
          <select
            id={`${idPrefix}-effort`}
            className={NATIVE_SELECT}
            value={effort}
            onChange={(e) => setEffort(e.target.value)}
          >
            <option value="">Default</option>
            {EFFORTS.map((e) => (
              <option key={e} value={e}>
                {e}
              </option>
            ))}
          </select>
        </div>
        {provider === "openai_compat" && (
          <div className="grid gap-2">
            <Label htmlFor={`${idPrefix}-base-url`}>Base URL</Label>
            <Input
              id={`${idPrefix}-base-url`}
              type="url"
              placeholder="https://example.com/v1"
              value={baseUrl}
              onChange={(e) => setBaseUrl(e.target.value)}
            />
          </div>
        )}
      </div>
    </SettingFrame>
  );
}
