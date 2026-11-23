import { describe, expect, it } from "vitest";
import { stateLabel } from "@/lib/review";
import { sug } from "@/components/review/fixtures";

describe("stateLabel", () => {
  it("names Janus and its key for an MCP decision", () => {
    const s = sug({ status: "accepted", decided_by: "janus-desktop", decided_channel: "mcp" });
    expect(stateLabel(s)).toBe("Accepted by Janus (janus-desktop)");
  });

  it("keeps the other channels' labels", () => {
    expect(stateLabel(sug({ status: "accepted", decided_by: "bob@example.com", decided_channel: "web" }))).toBe("Accepted by bob@example.com");
    expect(stateLabel(sug({ status: "rejected", decided_by: "k", decided_channel: "api" }))).toBe("Rejected by k");
    expect(stateLabel(sug({ status: "accepted", auto_applied: true, decided_by: "ai", decided_channel: "auto" }))).toBe("Applied by AI");
  });
});
