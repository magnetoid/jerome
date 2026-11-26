import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/lib/api";
import { Button } from "@/components/ui/button";
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
import { errorToast, formatDate, useForbiddenWatch } from "./shared";

export const LEARNED_KEY = ["learned-words"] as const;

type LearnedWord = {
  id: number;
  language: "en" | "de";
  word: string;
  rule: string;
  reason: string | null;
  book_id: number | null;
  created_by: string;
  created_at: string;
};

const LANGUAGES = { en: "English", de: "German" } as const;

/** Words LanguageTool no longer marks in any book of their language ("Remember for all books"). */
export default function LearnedWordsSection() {
  const client = useQueryClient();
  const words = useQuery({
    queryKey: LEARNED_KEY,
    queryFn: () => apiFetch<LearnedWord[]>("/v1/learned-words"),
  });
  useForbiddenWatch(words.error);
  const [deleting, setDeleting] = useState<LearnedWord | null>(null);
  const remove = useMutation({
    mutationFn: (id: number) => apiFetch<void>(`/v1/learned-words/${id}`, { method: "DELETE" }),
    onError: errorToast,
    onSettled: () => client.invalidateQueries({ queryKey: LEARNED_KEY }),
  });

  return (
    <section className="space-y-3" aria-labelledby="h-learned">
      <h2 id="h-learned" className="text-lg font-semibold">
        Learned words
      </h2>
      <p className="text-sm text-muted-foreground">
        LanguageTool no longer marks these in any book of their language. Saved from the review card with Remember
        for all books. Deleting a word does not reopen the marks it rejected.
      </p>
      {words.isError ? (
        <p role="alert">Could not load the learned words.</p>
      ) : words.data?.length === 0 ? (
        <p className="text-sm text-muted-foreground">No learned words yet.</p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Word</TableHead>
              <TableHead>Language</TableHead>
              <TableHead>Rule</TableHead>
              <TableHead>Reason</TableHead>
              <TableHead>Added</TableHead>
              <TableHead className="text-right">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {(words.data ?? []).map((w) => (
              <TableRow key={w.id}>
                <TableCell className="font-medium">{w.word}</TableCell>
                <TableCell>{LANGUAGES[w.language] ?? w.language}</TableCell>
                <TableCell>{w.rule === "*" ? "any spelling rule" : w.rule}</TableCell>
                <TableCell className="whitespace-normal">{w.reason}</TableCell>
                <TableCell>
                  {formatDate(w.created_at)} · {w.created_by}
                </TableCell>
                <TableCell className="text-right">
                  <Button size="sm" variant="ghost" aria-label={`Delete ${w.word}`} onClick={() => setDeleting(w)}>
                    Delete
                  </Button>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}

      <AlertDialog open={deleting !== null} onOpenChange={(o) => !o && setDeleting(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete “{deleting?.word}”?</AlertDialogTitle>
            <AlertDialogDescription>
              LanguageTool marks it again in later checks. Marks it already rejected stay rejected.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={() => deleting && remove.mutate(deleting.id)}>Delete</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </section>
  );
}
