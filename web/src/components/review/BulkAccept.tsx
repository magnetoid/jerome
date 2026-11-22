import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { toast } from "sonner";
import { apiFetch, apiFetchPage } from "@/lib/api";
import { detailOf } from "@/lib/jobs";
import { openCountKey, type Suggestion } from "@/lib/review";
import { useRefreshReview } from "@/hooks/useDecide";
import { Button } from "@/components/ui/button";
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription,
  AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from "@/components/ui/alert-dialog";

interface Props {
  bookId: string;
  chapterId: string;
  category: string;
  onDone: () => void;
}

/** "Accept all N remaining <category> in this chapter", behind a confirmation. */
export default function BulkAccept({ bookId, chapterId, category, onDone }: Props) {
  const [confirming, setConfirming] = useState(false);
  const refresh = useRefreshReview(bookId);
  // The loaded pages may not hold the whole chapter, so the API counts.
  const count = useQuery({
    queryKey: openCountKey(bookId, chapterId, category),
    queryFn: () => {
      const q = new URLSearchParams({ chapter_id: chapterId, category, status: "open", limit: "1" });
      return apiFetchPage<Suggestion[]>(`/v1/books/${bookId}/suggestions?${q}`);
    },
    select: (r) => r.total,
  });
  const bulk = useMutation({
    mutationFn: () =>
      apiFetch<{ decided: number }>("/v1/suggestions/bulk", {
        method: "POST",
        json: {
          book_id: Number(bookId),
          filter: { chapter_id: Number(chapterId), category, status: "open" },
          action: "accept",
        },
      }),
    onSuccess: (r) => {
      toast.success(`Accepted ${r.decided} ${category} suggestion${r.decided === 1 ? "" : "s"}`);
      onDone();
    },
    onError: (e) => toast.error(detailOf(e)),
    onSettled: () => refresh(),
  });

  const n = count.data ?? 0;
  if (n <= 0) return null;
  return (
    <>
      <Button variant="outline" onClick={() => setConfirming(true)} disabled={bulk.isPending}>
        {`Accept all ${n} remaining ${category} in this chapter`}
      </Button>
      <AlertDialog open={confirming} onOpenChange={setConfirming}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{`Accept all ${n} remaining ${category}?`}</AlertDialogTitle>
            <AlertDialogDescription>
              {`Every open ${category} suggestion in this chapter is accepted as your decision, including ones not loaded on screen yet. You can undo them one at a time afterwards.`}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Keep reviewing</AlertDialogCancel>
            <AlertDialogAction onClick={() => bulk.mutate()}>{`Accept all ${n}`}</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
