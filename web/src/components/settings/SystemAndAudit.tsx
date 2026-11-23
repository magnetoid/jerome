import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "@/lib/api";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { AUDIT_KEY, formatDateTime, SECRET_KEYS, type SystemInfo } from "./shared";

const STORAGE_LABEL = { ready: "Ready", missing: "Not set", invalid: "Invalid key" } as const;

export function SystemSection({ system }: { system: SystemInfo }) {
  const items: [string, string][] = [
    ["Database", system.database_url],
    ["Data directory", system.data_dir],
    ["Web directory", system.web_dir ?? "None"],
    ["Trusted proxies", system.trusted_proxies.length ? system.trusted_proxies.join(", ") : "None"],
    ["Secure cookies", system.cookie_secure ? "Yes" : "No"],
    ["Secret storage", STORAGE_LABEL[system.secret_storage]],
  ];
  return (
    <section className="space-y-3" aria-labelledby="h-system">
      <h2 id="h-system" className="text-lg font-semibold">
        System
      </h2>
      <dl className="grid grid-cols-[max-content_1fr] gap-x-6 gap-y-1 text-sm">
        {items.map(([k, v]) => (
          <div key={k} className="contents">
            <dt className="text-muted-foreground">{k}</dt>
            <dd className="break-all">{v}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

type Audit = {
  id: number;
  at: string;
  actor: string;
  key: string;
  action: string;
  before: unknown;
  after: unknown;
};

function show(v: unknown): string {
  return v === null || v === undefined ? "none" : typeof v === "string" ? v : JSON.stringify(v);
}

export function AuditSection() {
  const audit = useQuery({
    queryKey: AUDIT_KEY,
    queryFn: () => apiFetch<Audit[]>("/v1/settings/audit"),
  });
  return (
    <section className="space-y-3" aria-labelledby="h-audit">
      <h2 id="h-audit" className="text-lg font-semibold">
        Recent changes
      </h2>
      {audit.isError ? (
        <p role="alert">Could not load the recent changes.</p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>When</TableHead>
              <TableHead>Who</TableHead>
              <TableHead>Setting</TableHead>
              <TableHead>Action</TableHead>
              <TableHead>Change</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {(audit.data ?? []).map((a) => (
              <TableRow key={a.id}>
                <TableCell>{formatDateTime(a.at)}</TableCell>
                <TableCell>{a.actor}</TableCell>
                <TableCell>{a.key}</TableCell>
                <TableCell>{a.action}</TableCell>
                <TableCell className="font-mono text-xs break-all">
                  {SECRET_KEYS.includes(a.key) ? "" : `${show(a.before)} → ${show(a.after)}`}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </section>
  );
}
