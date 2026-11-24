import { Button } from "@/components/ui/button";
import { useChapterAiCheck } from "@/hooks/useAiCheck";

/** "Check LT marks in this chapter": the AI's verdict on each unchecked LanguageTool mark. */
export default function CheckLtMarks({ bookId, chapterId }: { bookId: string; chapterId: string }) {
  const check = useChapterAiCheck(bookId, chapterId);
  return (
    <Button
      variant="outline"
      onClick={() => check.mutate()}
      disabled={check.isPending}
      title="Asks the AI about each LanguageTool mark not checked yet, up to 40 per click. A paid model call per paragraph."
    >
      {check.isPending ? "Checking LT marks…" : "Check LT marks in this chapter"}
    </Button>
  );
}
