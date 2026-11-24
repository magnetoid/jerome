import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import PublicationSection from "./PublicationSection";
import { json, mockApi, renderApp } from "@/test-utils";
import { PUBLICATION, bodyOf, callsTo } from "./fixtures";

afterEach(() => vi.unstubAllGlobals());

describe("PublicationSection", () => {
  it("saves authors as a list, clears a field with null, shows the ISBN error from a 422", async () => {
    const f = mockApi({
      "GET /v1/books/1/publication": () => json(200, PUBLICATION),
      "PUT /v1/books/1/publication": (init) => {
        const body = JSON.parse(init.body ?? "{}");
        if (body.isbn_epub === "123") return json(422, { detail: "ISBN '123' is not a valid ISBN-13" });
        return json(200, { ...PUBLICATION, ...body });
      },
    });
    renderApp(<PublicationSection bookId="1" />);
    const authors = await screen.findByLabelText("Authors (one per line)");
    await userEvent.clear(authors);
    await userEvent.type(authors, "Ann Author{enter}Bob Writer{enter}");
    await userEvent.clear(screen.getByLabelText("Publisher"));
    await userEvent.click(screen.getByRole("button", { name: "Save details" }));
    await waitFor(() => expect(callsTo(f, "PUT", "/v1/books/1/publication")).toHaveLength(1));
    const sent = bodyOf(callsTo(f, "PUT", "/v1/books/1/publication")[0]);
    expect(sent.authors).toEqual(["Ann Author", "Bob Writer"]);
    expect(sent.publisher).toBeNull();
    expect(sent.year).toBe(2026);
    expect(sent.trim_size).toBe("6x9in");

    await userEvent.type(screen.getByLabelText("ISBN (EPUB)"), "123");
    await userEvent.click(screen.getByRole("button", { name: "Save details" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("not a valid ISBN-13");
  });

  it('warns "Shops need a cover" without one and shows the cover preview with one', async () => {
    mockApi({
      "GET /v1/books/1/publication": () => json(200, PUBLICATION),
      "PUT /v1/books/1/cover": () => json(200, { ...PUBLICATION, has_cover: true }),
    });
    const { container } = renderApp(<PublicationSection bookId="1" />);
    expect(await screen.findByText(/Shops need a cover/)).toBeInTheDocument();
    expect(screen.queryByRole("img", { name: "Cover" })).toBeNull();
    const png = new File(["x"], "cover.png", { type: "image/png" });
    await userEvent.upload(container.querySelector("input[type=file]")!, png);
    const img = await screen.findByRole("img", { name: "Cover" });
    expect(img.getAttribute("src")).toMatch(/^\/v1\/books\/1\/cover/);
    expect(screen.queryByText(/Shops need a cover/)).toBeNull();
    expect(screen.getByRole("button", { name: "Remove cover" })).toBeInTheDocument();
  });

  it("shows why a cover was refused", async () => {
    mockApi({
      "GET /v1/books/1/publication": () => json(200, PUBLICATION),
      "PUT /v1/books/1/cover": () => json(422, { detail: "The cover is 1000 px on its long side; shops need at least 1400" }),
    });
    const { container } = renderApp(<PublicationSection bookId="1" />);
    await screen.findByText(/Shops need a cover/);
    await userEvent.upload(container.querySelector("input[type=file]")!, new File(["x"], "c.jpg", { type: "image/jpeg" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("shops need at least 1400");
  });
});
