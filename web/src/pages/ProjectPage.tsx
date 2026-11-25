import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription,
  AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import StageBadge from "@/components/StageBadge";
import StartPanel from "@/components/project/StartPanel";
import ProgressPanel from "@/components/project/ProgressPanel";
import AttentionPanel from "@/components/project/AttentionPanel";
import SummaryPanel from "@/components/project/SummaryPanel";
import JobsList from "@/components/project/JobsList";
import ExportMenu from "@/components/project/ExportMenu";
import PublicationSection from "@/components/project/PublicationSection";
import FinishedFiles from "@/components/project/FinishedFiles";
import ReviewUpload from "@/components/project/ReviewUpload";
import SettingsTab from "@/components/project/SettingsTab";
import TranslationPanel, { FlaggedParagraphs, SourceBookLink } from "@/components/project/TranslationPanel";
import { PROJECTS_KEY, useProjects, type Project } from "@/hooks/useProjects";
import { useBookJobs } from "@/hooks/useBookJobs";
import { bookKey, detailOf, latestReviewJob } from "@/lib/jobs";

interface Book {
  id: number;
  title: string;
  format: "docx" | "epub";
  /** The hidden source book of a translation; absent from older servers. */
  source_book_id?: number | null;
}

function MainPanel({ project, format }: { project: Project; format: string }) {
  const id = String(project.id);
  switch (project.stage) {
    case "importing":
      return <p className="text-sm text-muted-foreground">Importing the PDF…</p>;
    case "set_up":
      return project.work === "translate"
        ? <TranslationPanel project={project} format={format} />
        : <StartPanel bookId={id} openCount={project.open_count} />;
    case "translated":
      return <TranslationPanel project={project} format={format} />;
    case "translating":
    case "proofreading":
      return project.active_job
        ? <ProgressPanel bookId={id} jobId={project.active_job.id} />
        : <p className="text-sm text-muted-foreground">Starting…</p>;
    case "needs_attention":
      return <AttentionPanel bookId={id} activeJobId={project.active_job?.id ?? null} />;
    default:
      return <SummaryPanel project={project} />;
  }
}

export default function ProjectPage() {
  const id = useParams().id ?? "";
  const navigate = useNavigate();
  const client = useQueryClient();
  const projects = useProjects();
  const book = useQuery({
    queryKey: bookKey(id),
    queryFn: () => apiFetch<Book>(`/v1/books/${id}`),
  });
  const project = projects.data?.find((p) => String(p.id) === id);
  const jobs = useBookJobs(id, project?.active_job);
  const [again, setAgain] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);

  const remove = useMutation({
    mutationFn: () => apiFetch(`/v1/books/${id}`, { method: "DELETE" }),
    onSuccess: async () => {
      await client.invalidateQueries({ queryKey: PROJECTS_KEY });
      navigate("/");
    },
  });

  if (projects.isPending) return <main className="p-8 text-sm text-muted-foreground">Loading…</main>;
  if (projects.isError) {
    return <main className="p-8"><p role="alert" className="text-destructive">{detailOf(projects.error)}</p></main>;
  }
  if (!project) {
    return <main className="w-full p-8"><h1 className="text-2xl font-semibold">Project not found</h1></main>;
  }

  const idle = project.active_job === null;
  const format = book.data?.format ?? project.format;
  // A cancelled job is not a stage of its own (the stage shows what is there to review), but
  // the cancel promised it could be resumed: offer that until another job takes its place.
  const resumable =
    idle && project.stage !== "needs_attention" && latestReviewJob(jobs.data)?.status === "cancelled";
  // set_up and translated already show the proofreading start (or the translation's).
  const rerun = idle && project.stage !== "set_up" && project.stage !== "translated";
  const sourceId = book.data?.source_book_id;
  const counts = project.work === "translate" ? project.translation ?? {} : {};
  const flaggedCount = counts.flagged ?? 0;
  // The server refuses to export a translation while any paragraph is pending or flagged.
  const untranslated = (counts.pending ?? 0) + flaggedCount;
  // translated shows the flagged paragraphs in its own panel; before it there are none to act on.
  const laterFlagged =
    flaggedCount > 0 && !["set_up", "translating", "translated"].includes(project.stage);

  return (
    <main className="w-full space-y-6 p-4 sm:p-8">
      <header className="flex flex-wrap items-center gap-3">
        <h1 className="text-2xl font-semibold">{project.title}</h1>
        <StageBadge stage={project.stage} />
        {sourceId != null && <SourceBookLink sourceId={sourceId} variant={project.source_variant} />}
        <Link to={`/activity?book=${id}`} className="ml-auto text-sm text-muted-foreground underline underline-offset-4">
          Activity for this book
        </Link>
      </header>

      <Tabs defaultValue="overview">
        <TabsList>
          <TabsTrigger value="overview">Overview</TabsTrigger>
          <TabsTrigger value="settings">Settings</TabsTrigger>
        </TabsList>

        <TabsContent value="overview" className="space-y-8 pt-4">
          <section className="space-y-4">
            <MainPanel project={project} format={format} />
            {resumable && (
              <div className="border-t pt-4">
                <AttentionPanel bookId={id} activeJobId={null} />
              </div>
            )}
            {rerun && (
              again ? (
                <div className="space-y-4 border-t pt-4">
                  <h2 className="text-lg font-medium">Run proofreading again</h2>
                  <StartPanel bookId={id} openCount={project.open_count} />
                </div>
              ) : (
                <Button variant="outline" onClick={() => setAgain(true)}>Run proofreading again</Button>
              )
            )}
          </section>
          {laterFlagged && (
            <div className="border-t pt-4">
              <FlaggedParagraphs project={project} format={format} />
            </div>
          )}
          <JobsList jobs={jobs} />
          <ExportMenu bookId={id} format={format} untranslated={untranslated} />
          <div className="space-y-8 border-t pt-4">
            <PublicationSection bookId={id} />
            <FinishedFiles bookId={id} untranslated={untranslated} />
          </div>
          {format === "docx" && <ReviewUpload bookId={id} />}
          <section className="border-t pt-4">
            <Button variant="destructive" onClick={() => setConfirmDelete(true)}>Delete project</Button>
            {remove.isError && <p role="alert" className="mt-2 text-sm text-destructive">{detailOf(remove.error)}</p>}
          </section>
        </TabsContent>

        <TabsContent value="settings" className="pt-4">
          <SettingsTab bookId={id} />
        </TabsContent>
      </Tabs>

      <AlertDialog open={confirmDelete} onOpenChange={setConfirmDelete}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete {project.title}?</AlertDialogTitle>
            <AlertDialogDescription>
              The book, its suggestions and decisions will be permanently deleted. This cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Keep it</AlertDialogCancel>
            <AlertDialogAction onClick={() => remove.mutate()}>Delete</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </main>
  );
}
