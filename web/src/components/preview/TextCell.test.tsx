import { describe, expect, it } from "vitest";
import { render } from "@testing-library/react";
import TextCell from "./TextCell";

const DOG = { text: "the dog", marks: [], changes: [{ start: 4, end: 7, removed: "cat" }] };

describe("TextCell", () => {
  it("shows what proofreading removed before what it put in", () => {
    const { container } = render(<TextCell text={DOG} kind="paragraph" changes />);
    const del = container.querySelector("del");
    const ins = container.querySelector("ins");
    expect(del).toHaveTextContent("cat");
    expect(ins).toHaveTextContent("dog");
    expect(del!.compareDocumentPosition(ins!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(container).toHaveTextContent("the catdog");
  });

  it("shows the plain text with changes off", () => {
    const { container } = render(<TextCell text={DOG} kind="paragraph" changes={false} />);
    expect(container).toHaveTextContent(/^the dog$/);
    expect(container.querySelector("del, ins")).toBeNull();
  });

  it("shows a pure insertion and a pure deletion", () => {
    const text = { text: "a big cat", marks: [], changes: [{ start: 2, end: 6, removed: "" }, { start: 9, end: 9, removed: "s" }] };
    const { container } = render(<TextCell text={text} kind="paragraph" changes />);
    expect(container.querySelector("ins")).toHaveTextContent("big");
    expect(container.querySelector("del")).toHaveTextContent("s");
  });

  it("counts the API's offsets in code points, not UTF-16 units", () => {
    const text = { text: "\u{1F600} dog", marks: [], changes: [{ start: 2, end: 5, removed: "cat" }] };
    const { container } = render(<TextCell text={text} kind="paragraph" changes />);
    expect(container.querySelector("ins")).toHaveTextContent(/^dog$/);
    expect(container).toHaveTextContent("\u{1F600} catdog");
  });
});
