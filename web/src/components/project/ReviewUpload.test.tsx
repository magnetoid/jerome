import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ReviewUpload from "./ReviewUpload";
import { json, renderApp } from "@/test-utils";
import { callsTo, pageApi } from "./fixtures";

afterEach(() => vi.unstubAllGlobals());

const docx = () => new File(["x"], "reviewed.docx", {
  type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
});

describe("ReviewUpload", () => {
  it("uploads the file and shows counts, conflicts and notes", async () => {
    const f = pageApi({
      "POST /v1/books/1/review-imports": () => json(201, {
        review_import_id: 5, counts: { accepted: 12, rejected: 3 },
        unmatched_block_ids: [4, 9], conflicts: [7], notes: ["Tracked change on a footnote was skipped."],
      }),
    });
    const { container } = renderApp(<ReviewUpload bookId="1" />);
    await userEvent.upload(container.querySelector("input[type=file]")!, docx());
    await waitFor(() => expect(callsTo(f, "POST", "/v1/books/1/review-imports")).toHaveLength(1));
    const form = (callsTo(f, "POST", "/v1/books/1/review-imports")[0][1] as RequestInit).body as FormData;
    expect((form.get("file") as File).name).toBe("reviewed.docx");
    expect(await screen.findByText(/accepted/i)).toBeInTheDocument();
    expect(screen.getByText("12")).toBeInTheDocument();
    expect(screen.getByText(/1 conflict/i)).toBeInTheDocument();
    expect(screen.getByText(/2 .*could not be matched/i)).toBeInTheDocument();
    expect(screen.getByText(/footnote was skipped/)).toBeInTheDocument();
  });

  it("shows the API detail on error", async () => {
    pageApi({ "POST /v1/books/1/review-imports": () => json(400, { detail: "not a review copy of this book" }) });
    const { container } = renderApp(<ReviewUpload bookId="1" />);
    await userEvent.upload(container.querySelector("input[type=file]")!, docx());
    expect(await screen.findByRole("alert")).toHaveTextContent("not a review copy of this book");
  });
});
