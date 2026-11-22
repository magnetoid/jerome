import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
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
import ReviewUpload from "@/components/project/ReviewUpload";
import SettingsTab from "@/components/project/SettingsTab";
import { PROJECTS_KEY, useProjects, type Project } from "@/hooks/useProjects";
import { bookKey, detailOf } from "@/lib/jobs";

interface Book {
  id: number;
  title: string;
  format: "docx" | "epub";
}

function MainPanel({ project }: { project: Project }) {
  const id = String(project.id);
  switch (project.stage) {
    case "set_up":
      return <StartPanel bookId={id} />;
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
  const project = projects.data.find((p) => String(p.id) === id);
  if (!project) {
    return <main className="mx-auto max-w-3xl p-8"><h1 className="text-2xl font-semibold">Project not found</h1></main>;
  }

  const idle = project.active_job === null;
  const format = book.data?.format ?? project.format;

  return (
    <main className="mx-auto max-w-4xl space-y-6 p-4 sm:p-8">
      <header className="flex flex-wrap items-center gap-3">
        <h1 className="text-2xl font-semibold">{project.title}</h1>
        <StageBadge stage={project.stage} />
      </header>

      <Tabs defaultValue="overview">
        <TabsList>
          <TabsTrigger value="overview">Overview</TabsTrigger>
          <TabsTrigger value="settings">Settings</TabsTrigger>
        </TabsList>

        <TabsContent value="overview" className="space-y-8 pt-4">
          <section className="space-y-4">
            <MainPanel project={project} />
            {idle && project.stage !== "set_up" && (
              again ? (
                <div className="space-y-4 border-t pt-4">
                  <h2 className="text-lg font-medium">Run proofreading again</h2>
                  <StartPanel bookId={id} />
                </div>
              ) : (
                <Button variant="outline" onClick={() => setAgain(true)}>Run proofreading again</Button>
              )
            )}
          </section>
          <JobsList bookId={id} />
          <ExportMenu bookId={id} format={format} />
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
