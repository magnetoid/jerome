import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

export type TempPassword = { name: string; password: string };

/**
 * Shows a temporary password once. The caller keeps it in component state and clears it on
 * close. Only Done closes it: Escape, a click outside or a stray close button would lose the
 * password, and getting another one means a new reset.
 */
export default function TempPasswordDialog({
  value,
  onClose,
}: {
  value: TempPassword | null;
  onClose: () => void;
}) {
  async function copy() {
    if (!value) return;
    try {
      await navigator.clipboard.writeText(value.password);
      toast.success("Copied to clipboard");
    } catch {
      toast.error("Could not copy. Select the password and copy it by hand.");
    }
  }

  return (
    <Dialog open={value !== null}>
      <DialogContent
        showCloseButton={false}
        onEscapeKeyDown={(e) => e.preventDefault()}
        onInteractOutside={(e) => e.preventDefault()}
      >
        <DialogHeader>
          <DialogTitle>Temporary password</DialogTitle>
          <DialogDescription>
            Give this to {value?.name}. It is shown only once and they must change it at their next
            login.
          </DialogDescription>
        </DialogHeader>
        <div className="flex items-center gap-2">
          <code className="flex-1 rounded-md bg-muted px-3 py-2 font-mono text-sm break-all select-all">
            {value?.password}
          </code>
          <Button type="button" variant="outline" onClick={() => void copy()}>
            Copy
          </Button>
        </div>
        <DialogFooter>
          <Button type="button" onClick={onClose}>
            Done
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
