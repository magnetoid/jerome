import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import DropZone from "@/components/DropZone";
import Loading from "@/components/Loading";
import Logo from "@/components/Logo";
import StageBadge, { type Stage } from "@/components/StageBadge";
import { Progress } from "@/components/ui/progress";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableRow } from "@/components/ui/table";

describe("Loading", () => {
  it("announces itself once and hides its shapes from screen readers", () => {
    const { container } = render(<Loading />);
    const status = screen.getByRole("status");
    expect(status).toHaveTextContent(/^Loading…$/);
    expect(screen.getAllByText("Loading…")).toHaveLength(1);
    expect(screen.getByText("Loading…")).toHaveClass("sr-only");
    const shapes = container.querySelectorAll(".skeleton");
    expect(shapes).toHaveLength(3);
    for (const shape of shapes) expect(shape).toHaveAttribute("aria-hidden", "true");
  });

  it("shows the shapes it is given in place of its own", () => {
    const { container } = render(
      <Loading className="grid">
        <Skeleton className="h-10" data-testid="row" />
      </Loading>,
    );
    expect(screen.getByRole("status")).toHaveClass("grid");
    expect(container.querySelectorAll(".skeleton")).toHaveLength(1);
    expect(screen.getByTestId("row")).toHaveClass("skeleton", "h-10");
    expect(screen.getByTestId("row")).toHaveAttribute("aria-hidden", "true");
  });
});

describe("Progress", () => {
  const indicator = (c: HTMLElement) => c.querySelector('[data-slot="progress-indicator"]');

  it("shimmers while the work it measures is running", () => {
    const { container } = render(<Progress value={40} running aria-label="Work" />);
    expect(screen.getByRole("progressbar", { name: "Work" })).toHaveAttribute("data-running", "true");
    expect(indicator(container)).toHaveClass("shimmer");
  });

  it("is still when the work is not running", () => {
    const { container } = render(<Progress value={40} aria-label="Work" />);
    expect(screen.getByRole("progressbar")).not.toHaveAttribute("data-running");
    expect(indicator(container)).not.toHaveClass("shimmer");
  });

  it("is as wide as its value", () => {
    const { container, rerender } = render(<Progress value={40} aria-label="Work" />);
    expect(indicator(container)).toHaveStyle({ width: "40%" });
    rerender(<Progress aria-label="Work" />);
    expect(indicator(container)).toHaveStyle({ width: "0%" });
  });
});

describe("StageBadge", () => {
  const LABELS: Record<Stage, string> = {
    importing: "Importing",
    set_up: "Set up",
    translating: "Translating",
    translated: "Translated",
    proofreading: "Proofreading",
    needs_attention: "Needs attention",
    to_review: "To review",
    done: "Done",
  };
  const RUNNING: Stage[] = ["importing", "translating", "proofreading"];

  it.each(Object.keys(LABELS) as Stage[])("%s: a pulsing dot only while work is running", (stage) => {
    const { container } = render(<StageBadge stage={stage} />);
    const badge = container.querySelector(`[data-stage="${stage}"]`);
    expect(badge).toHaveTextContent(new RegExp(`^${LABELS[stage]}$`));
    const dot = container.querySelector("[data-running-dot]");
    if (RUNNING.includes(stage)) {
      expect(dot).toHaveClass("pulse-dot");
      expect(dot).toHaveAttribute("aria-hidden", "true");
    } else {
      expect(dot).toBeNull();
    }
  });
});

describe("Logo", () => {
  it("is the mark and the name", () => {
    const { container } = render(<Logo />);
    expect(screen.getByText("Jerome")).toHaveClass("font-display");
    expect(container.querySelector("[data-logo-mark]")).toHaveAttribute("aria-hidden", "true");
  });

  it("is the mark alone when asked", () => {
    const { container } = render(<Logo wordmark={false} />);
    expect(screen.queryByText("Jerome")).toBeNull();
    expect(container.querySelector("[data-logo-mark]")).toHaveAttribute("aria-hidden", "true");
  });
});

describe("DropZone", () => {
  it("reacts while a file is dragged over it", () => {
    render(<DropZone file={null} onFile={() => {}} onReject={() => {}} />);
    const zone = screen.getByRole("button", { name: "Choose a file or drop it here" });
    expect(zone).not.toHaveAttribute("data-over");
    fireEvent.dragOver(zone);
    expect(zone).toHaveAttribute("data-over", "true");
    expect(zone).toHaveClass("border-brand");
    fireEvent.dragLeave(zone);
    expect(zone).not.toHaveAttribute("data-over");
    expect(zone).not.toHaveClass("border-brand");
  });
});

describe("Table", () => {
  it("never scrolls up and down: a row that rises in must not bring a scrollbar", () => {
    const { container } = render(
      <Table>
        <TableBody>
          <TableRow className="row-enter">
            <TableCell>One</TableCell>
          </TableRow>
        </TableBody>
      </Table>,
    );
    const frame = container.querySelector('[data-slot="table-container"]');
    expect(frame).toHaveClass("overflow-x-auto", "overflow-y-hidden");
  });
});
