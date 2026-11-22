import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { ApiError, apiFetch } from "@/lib/api";
import { useMe } from "@/hooks/useMe";
import TempPasswordDialog, { type TempPassword } from "@/components/TempPasswordDialog";
import { Badge } from "@/components/ui/badge";
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
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

type Member = {
  id: number;
  email: string;
  name: string;
  active: boolean;
  must_change_password: boolean;
  last_login_at: string | null;
  created_at: string;
};
type Result = { user: Member; temporary_password: string | null };
type Patch = { name?: string; active?: boolean; reset_password?: boolean };

const USERS_KEY = ["users"] as const;

function lastLogin(iso: string | null): string {
  if (!iso) return "Never";
  return new Date(iso).toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

export default function TeamPage() {
  const client = useQueryClient();
  const me = useMe().data;
  const users = useQuery({ queryKey: USERS_KEY, queryFn: () => apiFetch<Member[]>("/v1/users") });

  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [temp, setTemp] = useState<TempPassword | null>(null);
  const [renaming, setRenaming] = useState<Member | null>(null);
  const [renameTo, setRenameTo] = useState("");
  const [deactivating, setDeactivating] = useState<Member | null>(null);
  const [resetting, setResetting] = useState<Member | null>(null);

  const onError = (err: unknown) =>
    toast.error(err instanceof ApiError ? err.detail : "Something went wrong");
  const refresh = () => client.invalidateQueries({ queryKey: USERS_KEY });

  // The temporary password goes straight into component state and is stripped
  // before the mutation resolves, so it never reaches react-query's mutation cache.
  const keepOnce = (res: Result): Member => {
    if (res.temporary_password) {
      setTemp({ name: res.user.name, password: res.temporary_password });
    }
    return res.user;
  };

  const add = useMutation({
    gcTime: 0,
    mutationFn: async (body: { email: string; name: string }) =>
      keepOnce(await apiFetch<Result>("/v1/users", { method: "POST", json: body })),
    onSuccess: () => {
      setEmail("");
      setName("");
    },
    onError,
    onSettled: refresh,
  });

  const patch = useMutation({
    gcTime: 0,
    mutationFn: async ({ id, body }: { id: number; body: Patch }) =>
      keepOnce(await apiFetch<Result>(`/v1/users/${id}`, { method: "PATCH", json: body })),
    onError,
    onSettled: refresh,
  });

  function onAdd(e: FormEvent) {
    e.preventDefault();
    add.mutate({ email: email.trim(), name: name.trim() });
  }

  function onRename(e: FormEvent) {
    e.preventDefault();
    if (!renaming) return;
    patch.mutate({ id: renaming.id, body: { name: renameTo.trim() } });
    setRenaming(null);
  }

  return (
    <main className="mx-auto max-w-4xl space-y-8 p-6">
      <h1 className="text-2xl font-semibold">Team</h1>

      <form onSubmit={onAdd} className="flex flex-wrap items-end gap-3" aria-label="Add member">
        <div className="grid gap-2">
          <Label htmlFor="add-email">Email</Label>
          <Input
            id="add-email"
            type="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </div>
        <div className="grid gap-2">
          <Label htmlFor="add-name">Name</Label>
          <Input
            id="add-name"
            required
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </div>
        <Button type="submit" disabled={add.isPending}>
          Add member
        </Button>
      </form>

      {users.isError ? (
        <p role="alert">Could not load the team.</p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Name</TableHead>
              <TableHead>Email</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Last login</TableHead>
              <TableHead className="text-right">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {(users.data ?? []).map((u) => {
              const self = me?.id != null && me.id === u.id;
              return (
                <TableRow key={u.id}>
                  <TableCell className="font-medium">{u.name}</TableCell>
                  <TableCell>{u.email}</TableCell>
                  <TableCell className="space-x-2">
                    <Badge variant={u.active ? "default" : "secondary"}>
                      {u.active ? "Active" : "Inactive"}
                    </Badge>
                    {u.must_change_password && (
                      <span className="text-xs text-muted-foreground">Must change password</span>
                    )}
                  </TableCell>
                  <TableCell>{lastLogin(u.last_login_at)}</TableCell>
                  <TableCell className="space-x-2 text-right">
                    <Button
                      size="sm"
                      variant="ghost"
                      aria-label={`Rename ${u.name}`}
                      onClick={() => {
                        setRenaming(u);
                        setRenameTo(u.name);
                      }}
                    >
                      Rename
                    </Button>
                    {u.active ? (
                      <Button
                        size="sm"
                        variant="ghost"
                        aria-label={`Deactivate ${u.name}`}
                        onClick={() => setDeactivating(u)}
                      >
                        Deactivate
                      </Button>
                    ) : (
                      <Button
                        size="sm"
                        variant="ghost"
                        aria-label={`Reactivate ${u.name}`}
                        onClick={() => patch.mutate({ id: u.id, body: { active: true } })}
                      >
                        Reactivate
                      </Button>
                    )}
                    {self ? (
                      <Button size="sm" variant="ghost" asChild>
                        <Link to="/change-password">Change password</Link>
                      </Button>
                    ) : (
                      <Button
                        size="sm"
                        variant="ghost"
                        aria-label={`Reset password for ${u.name}`}
                        onClick={() => setResetting(u)}
                      >
                        Reset password
                      </Button>
                    )}
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      )}

      <TempPasswordDialog value={temp} onClose={() => setTemp(null)} />

      <Dialog open={renaming !== null} onOpenChange={(o) => !o && setRenaming(null)}>
        <DialogContent>
          <form onSubmit={onRename} className="grid gap-4">
            <DialogHeader>
              <DialogTitle>Rename member</DialogTitle>
            </DialogHeader>
            <div className="grid gap-2">
              <Label htmlFor="rename-name">Name</Label>
              <Input
                id="rename-name"
                required
                value={renameTo}
                onChange={(e) => setRenameTo(e.target.value)}
              />
            </div>
            <DialogFooter>
              <Button type="submit">Save</Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      <AlertDialog open={deactivating !== null} onOpenChange={(o) => !o && setDeactivating(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Deactivate {deactivating?.name}?</AlertDialogTitle>
            <AlertDialogDescription>
              They will be signed out and will not be able to log in until you reactivate them.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={() =>
                deactivating && patch.mutate({ id: deactivating.id, body: { active: false } })
              }
            >
              Deactivate
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <AlertDialog open={resetting !== null} onOpenChange={(o) => !o && setResetting(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Reset {resetting?.name}'s password?</AlertDialogTitle>
            <AlertDialogDescription>
              This signs them out everywhere and replaces their password with a temporary one,
              shown to you once. Their current password stops working.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={() =>
                resetting && patch.mutate({ id: resetting.id, body: { reset_password: true } })
              }
            >
              Reset password
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </main>
  );
}
