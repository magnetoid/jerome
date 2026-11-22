import { BrowserRouter, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import AppShell from "@/components/AppShell";
import LoginPage from "@/pages/LoginPage";
import ChangePasswordPage from "@/pages/ChangePasswordPage";
import TeamPage from "@/pages/TeamPage";
import ProjectsPage from "@/pages/ProjectsPage";
import NewProjectPage from "@/pages/NewProjectPage";
import ProjectPage from "@/pages/ProjectPage";
import ReviewPage from "@/pages/ReviewPage";
import ThemeProvider from "@/components/ThemeProvider";
import { Toaster } from "@/components/ui/sonner";

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
});

function Placeholder({ title }: { title: string }) {
  return (
    <main className="mx-auto max-w-3xl p-8">
      <h1 className="text-2xl font-semibold">{title}</h1>
    </main>
  );
}

export default function App() {
  return (
    <ThemeProvider>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/change-password" element={<ChangePasswordPage />} />
          <Route element={<AppShell />}>
            <Route path="/" element={<ProjectsPage />} />
            <Route path="/projects/new" element={<NewProjectPage />} />
            <Route path="/projects/:id" element={<ProjectPage />} />
            <Route path="/projects/:id/review/:chapterId?" element={<ReviewPage />} />
            <Route path="/team" element={<TeamPage />} />
            <Route path="*" element={<Placeholder title="Page not found" />} />
          </Route>
        </Routes>
      </BrowserRouter>
      <Toaster />
    </QueryClientProvider>
    </ThemeProvider>
  );
}
