import { useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { ApiError, apiFetch } from "@/lib/api";
import { ME_KEY, useMe, type Role } from "@/hooks/useMe";
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
import { NATIVE_SELECT } from "@/components/NativeSelect";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

type Member = {
  id: number;
  email: string;
  name: string;
  active: boolean;
  role: Role;
  must_change_password: boolean;
  last_login_at: string | null;
  created_at: string;
};
type Result = { user: Member; temporary_password: string | null };
type Patch = { name?: string; active?: boolean; reset_password?: boolean; role?: Role };

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
  const isSuperAdmin = me?.role === "super_admin";
  const users = useQuery({
    queryKey: USERS_KEY,
    queryFn: () => apiFetch<Member[]>("/v1/users"),
    enabled: isSuperAdmin,
  });

  // A super admin demoted elsewhere gets 403 here: re-read who I am so the page
  // switches to the member message.
  const forbidden = users.error instanceof ApiError && users.error.detail === "super admin only";
  useEffect(() => {
    if (forbidden) client.invalidateQueries({ queryKey: ME_KEY });
  }, [forbidden, client]);

  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [role, setRole] = useState<Role>("member");
  const [temp, setTemp] = useState<TempPassword | null>(null);
  const [renaming, setRenaming] = useState<Member | null>(null);
  const [renameTo, setRenameTo] = useState("");
  const [deactivating, setDeactivating] = useState<Member | null>(null);
  const [resetting, setResetting] = useState<Member | null>(null);
  // The dialog's text keeps showing its member while it animates closed.
  const [roleMember, setRoleMember] = useState<Member | null>(null);
  const [roleOpen, setRoleOpen] = useState(false);

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
    mutationFn: async (body: { email: string; name: string; role: Role }) =>
      keepOnce(await apiFetch<Result>("/v1/users", { method: "POST", json: body })),
    onSuccess: () => {
      setEmail("");
      setName("");
      setRole("member");
    },
    onError,
    onSettled: refresh,
  });

  const patch = useMutation({
    gcTime: 0,
    mutationFn: async ({ id, body }: { id: number; body: Patch }) =>
      keepOnce(await apiFetch<Result>(`/v1/users/${id}`, { method: "PATCH", json: body })),
    onError,
    onSettled: (_data, _err, vars) => {
      // Changing your own role changes what you may see: wait for that instead of
      // refetching a list you may no longer be allowed to read.
      if (vars.body.role !== undefined && vars.id === me?.id) {
        return client.invalidateQueries({ queryKey: ME_KEY });
      }
      return refresh();
    },
  });

  function onAdd(e: FormEvent) {
    e.preventDefault();
    add.mutate({ email: email.trim(), name: name.trim(), role });
  }

  function onRename(e: FormEvent) {
    e.preventDefault();
    if (!renaming) return;
    patch.mutate({ id: renaming.id, body: { name: renameTo.trim() } });
    setRenaming(null);
  }

  return (
    <main className="w-full space-y-8 p-6">
      <h1 className="font-display text-3xl font-normal tracking-tight">Team</h1>

      {me && !isSuperAdmin ? (
        <p className="text-muted-foreground">Only a super admin can manage the team</p>
      ) : (
        <>
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
        <div className="grid gap-2">
          <Label htmlFor="add-role">Role</Label>
          <select
            id="add-role"
            className={NATIVE_SELECT}
            value={role}
            onChange={(e) => setRole(e.target.value as Role)}
          >
            <option value="member">Member</option>
            <option value="super_admin">Super admin</option>
          </select>
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
              <TableHead>Role</TableHead>
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
                  <TableCell>
                    <Badge variant={u.role === "super_admin" ? "default" : "outline"}>
                      {u.role === "super_admin" ? "Super admin" : "Member"}
                    </Badge>
                  </TableCell>
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
                    <Button
                      size="sm"
                      variant="ghost"
                      aria-label={
                        u.role === "super_admin"
                          ? `Make member: ${u.name}`
                          : `Make super admin: ${u.name}`
                      }
                      onClick={() => {
                        setRoleMember(u);
                        setRoleOpen(true);
                      }}
                    >
                      {u.role === "super_admin" ? "Make member" : "Make super admin"}
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

      <AlertDialog open={roleOpen} onOpenChange={setRoleOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {roleMember?.role === "super_admin"
                ? `Make ${roleMember?.name} a member?`
                : `Make ${roleMember?.name} a super admin?`}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {roleMember?.role === "super_admin"
                ? roleMember.id === me?.id
                  ? "You will no longer be able to manage the team."
                  : "They will no longer be able to manage the team."
                : "They will be able to add members, change roles and reset passwords."}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={() =>
                roleMember &&
                patch.mutate({
                  id: roleMember.id,
                  body: { role: roleMember.role === "super_admin" ? "member" : "super_admin" },
                })
              }
            >
              {roleMember?.role === "super_admin" ? "Make member" : "Make super admin"}
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
        </>
      )}
    </main>
  );
}
