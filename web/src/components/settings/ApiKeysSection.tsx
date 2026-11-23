import { useState, type FormEvent } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { apiFetch } from "@/lib/api";
import OneTimeSecretDialog from "@/components/OneTimeSecretDialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { errorToast, formatDate, KEYS_KEY, useForbiddenWatch, useRefreshSettings } from "./shared";

type ApiKey = { id: number; name: string; created_at: string; revoked_at: string | null };
type Created = { key: ApiKey; token: string };

export default function ApiKeysSection() {
  const refresh = useRefreshSettings();
  const keys = useQuery({
    queryKey: KEYS_KEY,
    queryFn: () => apiFetch<ApiKey[]>("/v1/api-keys"),
  });
  useForbiddenWatch(keys.error);

  const [name, setName] = useState("");
  const [token, setToken] = useState<string | null>(null);
  const [revoking, setRevoking] = useState<ApiKey | null>(null);

  // The token goes straight into component state and is stripped before the mutation
  // resolves, so it never reaches react-query's mutation cache.
  const create = useMutation({
    gcTime: 0,
    mutationFn: async (body: { name: string }) => {
      const res = await apiFetch<Created>("/v1/api-keys", { method: "POST", json: body });
      setToken(res.token);
      return res.key;
    },
    onSuccess: () => setName(""),
    onError: errorToast,
    onSettled: refresh,
  });
  const revoke = useMutation({
    mutationFn: (id: number) => apiFetch<ApiKey>(`/v1/api-keys/${id}`, { method: "DELETE" }),
    onError: errorToast,
    onSettled: refresh,
  });

  function onCreate(e: FormEvent) {
    e.preventDefault();
    create.mutate({ name: name.trim() });
  }

  return (
    <section className="space-y-3" aria-labelledby="h-keys">
      <h2 id="h-keys" className="text-lg font-semibold">
        API keys
      </h2>
      {keys.isError ? (
        <p role="alert">Could not load the API keys.</p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Name</TableHead>
              <TableHead>Created</TableHead>
              <TableHead>Status</TableHead>
              <TableHead className="text-right">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {(keys.data ?? []).map((k) => (
              <TableRow key={k.id}>
                <TableCell className="font-medium">{k.name}</TableCell>
                <TableCell>{formatDate(k.created_at)}</TableCell>
                <TableCell>
                  {k.revoked_at ? `Revoked ${formatDate(k.revoked_at)}` : "Active"}
                </TableCell>
                <TableCell className="text-right">
                  {!k.revoked_at && (
                    <Button
                      size="sm"
                      variant="ghost"
                      aria-label={`Revoke ${k.name}`}
                      onClick={() => setRevoking(k)}
                    >
                      Revoke
                    </Button>
                  )}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}

      <form onSubmit={onCreate} className="flex flex-wrap items-end gap-3" aria-label="Create key">
        <div className="grid gap-2">
          <Label htmlFor="key-name">Key name</Label>
          <Input id="key-name" required value={name} onChange={(e) => setName(e.target.value)} />
        </div>
        <Button type="submit" disabled={create.isPending}>
          Create key
        </Button>
      </form>

      <OneTimeSecretDialog
        title="New API key"
        description="Copy this key now. It won't be shown again."
        value={token}
        onClose={() => setToken(null)}
      />

      <AlertDialog open={revoking !== null} onOpenChange={(o) => !o && setRevoking(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Revoke {revoking?.name}?</AlertDialogTitle>
            <AlertDialogDescription>
              Anything using this key stops working right away. This cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={() => revoking && revoke.mutate(revoking.id)}>
              Revoke
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </section>
  );
}
