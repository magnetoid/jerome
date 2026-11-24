import { describe, expect, it } from "vitest";
import { screen } from "@testing-library/react";
import type { UseQueryResult } from "@tanstack/react-query";
import JobsList from "./JobsList";
import { renderApp } from "@/test-utils";
import { job } from "./fixtures";
import type { Job } from "@/lib/jobs";

const query = (data: unknown[]) => ({ data, isError: false }) as unknown as UseQueryResult<Job[]>;

describe("JobsList", () => {
  it("shows a publish job's warnings and a Download link when completed, and task.error when failed", () => {
    const done = job({
      id: 31, type: "publish", status: "completed", passes: ["pdf-book"], max_cost: "0", cost_actual: "0",
      stats: { file: "sea-31.pdf", warnings: ["The book has no cover", "2 suggestions are still open"] },
      tasks: [{ id: 4, kind: "publish", chapter_id: null, status: "done", chunks_done: 0, chunks_total: null, attempts: 1, error: null }],
    });
    const failed = job({
      id: 32, type: "publish", status: "failed", passes: ["epub-book"], max_cost: "0", cost_actual: "0",
      tasks: [{ id: 5, kind: "publish", chapter_id: null, status: "failed", chunks_done: 0, chunks_total: null, attempts: 1,
        error: "The finished file's text differs from the reviewed text near 'Arrival'" }],
    });
    renderApp(<JobsList jobs={query([failed, done])} />);
    expect(screen.getByText("Finished PDF")).toBeInTheDocument();
    expect(screen.getByText("Finished EPUB")).toBeInTheDocument();
    expect(screen.getByText("The book has no cover")).toBeInTheDocument();
    expect(screen.getByText("2 suggestions are still open")).toBeInTheDocument();
    const link = screen.getByRole("link", { name: "Download sea-31.pdf" });
    expect(link.getAttribute("href")).toBe("/v1/jobs/31/file");
    expect(link.hasAttribute("download")).toBe(true);
    expect(screen.getByText(/differs from the reviewed text near 'Arrival'/)).toBeInTheDocument();
    expect(screen.getAllByRole("link")).toHaveLength(1);
  });
});
