import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { toast } from "sonner";
import NewProjectPage, { PdfOptions } from "./NewProjectPage";
import { Where, json, mockApi, renderApp } from "@/test-utils";

vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }));

const CREATED = {
  id: 7, title: "My Novel", format: "docx", local_only: false,
  original_filename: "My Novel.docx", created_at: "2026-11-20T10:00:00Z",
};

beforeEach(() => vi.mocked(toast.error).mockClear());
afterEach(() => vi.unstubAllGlobals());

function setup(routes: Parameters<typeof mockApi>[0] = {}) {
  const f = mockApi({ "POST /v1/books": json(201, CREATED), ...routes });
  const view = renderApp(
    <>
      <Routes>
        <Route path="/projects/new" element={<NewProjectPage />} />
        <Route path="/projects/:id" element={<p>project page</p>} />
      </Routes>
      <Where />
    </>,
    "/projects/new",
  );
  return { f, ...view };
}

const docx = (name = "My Novel.docx") => new File(["x"], name);

async function choose(user: ReturnType<typeof userEvent.setup>, file = docx()) {
  await user.upload(screen.getByLabelText("Book file"), file);
}

function callsTo(f: ReturnType<typeof mockApi>, key: string) {
  return f.mock.calls.filter(([p, i]) => `${(i?.method ?? "GET").toUpperCase()} ${p}` === key);
}

describe("NewProjectPage", () => {
  it("pre-fills the title from the file name without its extension", async () => {
    const user = userEvent.setup();
    setup();
    await choose(user, docx("Great Expectations.final.docx"));
    expect(screen.getByLabelText("Title")).toHaveValue("Great Expectations.final");
  });

  it("does not overwrite a title the user edited when another file is chosen", async () => {
    const user = userEvent.setup();
    setup();
    await choose(user, docx("one.docx"));
    await user.clear(screen.getByLabelText("Title"));
    await user.type(screen.getByLabelText("Title"), "Mine");
    await choose(user, docx("two.epub"));
    expect(screen.getByLabelText("Title")).toHaveValue("Mine");
  });

  it("disables Create until a file is chosen", async () => {
    const user = userEvent.setup();
    setup();
    expect(screen.getByRole("button", { name: "Create project" })).toBeDisabled();
    await choose(user);
    expect(screen.getByRole("button", { name: "Create project" })).toBeEnabled();
  });

  it("uploads multipart with defaults and opens the project; no style PUT", async () => {
    const user = userEvent.setup();
    const { f, client } = setup();
    const spy = vi.spyOn(client, "invalidateQueries");
    await choose(user);
    await user.click(screen.getByRole("button", { name: "Create project" }));
    await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent("/projects/7"));
    const [, init] = callsTo(f, "POST /v1/books")[0];
    const body = init!.body as FormData;
    expect((body.get("file") as File).name).toBe("My Novel.docx");
    expect(body.get("variant")).toBe("en-GB");
    expect(body.get("title")).toBe("My Novel");
    expect(body.get("auto_apply")).toBe("true");
    expect(new Headers(init!.headers).has("Content-Type")).toBe(false);
    expect(callsTo(f, "PUT /v1/books/7/style-sheet")).toHaveLength(0);
    expect(spy).toHaveBeenCalledWith({ queryKey: ["projects"] });
  });

  it("sends auto_apply false and the chosen language", async () => {
    const user = userEvent.setup();
    const { f } = setup();
    await choose(user);
    await user.selectOptions(screen.getByLabelText("Language"), "de-AT");
    await user.click(
      screen.getByRole("checkbox", { name: /Let the AI apply clear-cut fixes/ }),
    );
    await user.click(screen.getByRole("button", { name: "Create project" }));
    await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent("/projects/7"));
    const body = callsTo(f, "POST /v1/books")[0][1]!.body as FormData;
    expect(body.get("variant")).toBe("de-AT");
    expect(body.get("auto_apply")).toBe("false");
  });

  it.each([
    [413, "Upload too large"],
    [422, "Not a readable DOCX or EPUB"],
    [500, "Server exploded"],
  ])("shows the API detail on a %i and stays on the page", async (status, detail) => {
    const user = userEvent.setup();
    setup({ "POST /v1/books": json(status, { detail }) });
    await choose(user);
    await user.click(screen.getByRole("button", { name: "Create project" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(detail);
    expect(screen.getByTestId("where")).toHaveTextContent("/projects/new");
    expect(screen.getByRole("button", { name: "Create project" })).toBeEnabled();
  });

  it("disables Create while the upload is pending", async () => {
    const user = userEvent.setup();
    let release!: () => void;
    setup({
      "POST /v1/books": () =>
        new Promise<Response>((r) => { release = () => r(json(201, CREATED)); }),
    });
    await choose(user);
    await user.click(screen.getByRole("button", { name: "Create project" }));
    await waitFor(() => expect(screen.getByRole("button", { name: /Creating/ })).toBeDisabled());
    release();
    await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent("/projects/7"));
  });

  it("sends only the changed house-style fields in a PUT after the upload", async () => {
    const user = userEvent.setup();
    const { f } = setup({ "PUT /v1/books/7/style-sheet": json(200, {}) });
    await choose(user);
    await user.click(screen.getByRole("button", { name: "House style" }));
    await user.type(screen.getByLabelText("House spellings"), "colour: color, colr\nfavour: favor");
    await user.type(screen.getByLabelText("Words to ignore"), "Zork, Narnia\nOz");
    await user.click(screen.getByRole("button", { name: "Create project" }));
    await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent("/projects/7"));
    const put = callsTo(f, "PUT /v1/books/7/style-sheet")[0][1]!;
    expect(JSON.parse(put.body as string)).toEqual({
      house_spellings: { colour: ["color", "colr"], favour: ["favor"] },
      ignore_words: ["Zork", "Narnia", "Oz"],
    });
  });

  it("sends quote_style only when it differs from the language default", async () => {
    const user = userEvent.setup();
    const { f } = setup({ "PUT /v1/books/7/style-sheet": json(200, {}) });
    await choose(user);
    await user.selectOptions(screen.getByLabelText("Language"), "de-CH");
    await user.click(screen.getByRole("button", { name: "House style" }));
    expect(screen.getByLabelText("Quote style")).toHaveValue("«»‹›");
    await user.selectOptions(screen.getByLabelText("Quote style"), "»«›‹");
    await user.click(screen.getByRole("button", { name: "Create project" }));
    await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent("/projects/7"));
    const put = callsTo(f, "PUT /v1/books/7/style-sheet")[0][1]!;
    expect(JSON.parse(put.body as string)).toEqual({ quote_style: "»«›‹" });
  });

  it("shows a parse error for a bad house-spelling line and does not upload", async () => {
    const user = userEvent.setup();
    const { f } = setup();
    await choose(user);
    await user.click(screen.getByRole("button", { name: "House style" }));
    await user.type(screen.getByLabelText("House spellings"), "no colon here");
    expect(screen.getByRole("alert")).toHaveTextContent("Line 1");
    expect(screen.getByRole("button", { name: "Create project" })).toBeDisabled();
    expect(callsTo(f, "POST /v1/books")).toHaveLength(0);
  });

  it("still opens the project and toasts when only the style-sheet save fails", async () => {
    const user = userEvent.setup();
    const { f } = setup({
      "PUT /v1/books/7/style-sheet": json(422, { detail: "bad quote style" }),
    });
    await choose(user);
    await user.click(screen.getByRole("button", { name: "House style" }));
    await user.type(screen.getByLabelText("Words to ignore"), "Zork");
    await user.click(screen.getByRole("button", { name: "Create project" }));
    await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent("/projects/7"));
    expect(toast.error).toHaveBeenCalledWith(
      "Project created, but the house style wasn't saved: bad quote style",
    );
    expect(callsTo(f, "POST /v1/books")).toHaveLength(1);
  });

  it("sends work=translate with the source and target variants", async () => {
    const user = userEvent.setup();
    const { f } = setup();
    await choose(user);
    await user.click(screen.getByRole("radio", { name: "Translate + proofread" }));
    await user.selectOptions(screen.getByLabelText("From"), "en-US");
    await user.selectOptions(screen.getByLabelText("To"), "de-CH");
    await user.type(screen.getByLabelText(/Translated title/), "Mein Roman");
    await user.click(screen.getByRole("button", { name: "House style" }));
    expect(screen.getByLabelText("Quote style")).toHaveValue("«»‹›");
    await user.click(screen.getByRole("button", { name: "Create project" }));
    await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent("/projects/7"));
    const body = callsTo(f, "POST /v1/books")[0][1]!.body as FormData;
    expect(body.get("work")).toBe("translate");
    expect(body.get("variant")).toBe("en-US");
    expect(body.get("target_variant")).toBe("de-CH");
    expect(body.get("translated_title")).toBe("Mein Roman");
    expect(body.get("title")).toBe("My Novel");
    // The quote style is the target language's default, so there is nothing to PUT.
    expect(callsTo(f, "PUT /v1/books/7/style-sheet")).toHaveLength(0);
  });

  it("proofread only sends work=proofread and no translation fields", async () => {
    const user = userEvent.setup();
    const { f } = setup();
    expect(screen.getByRole("radio", { name: "Proofread only" })).toBeChecked();
    expect(screen.queryByLabelText("To")).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/Translated title/)).not.toBeInTheDocument();
    await choose(user);
    await user.click(screen.getByRole("button", { name: "Create project" }));
    await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent("/projects/7"));
    const body = callsTo(f, "POST /v1/books")[0][1]!.body as FormData;
    expect(body.get("work")).toBe("proofread");
    expect(body.has("target_variant")).toBe(false);
    expect(body.has("translated_title")).toBe(false);
  });

  it("To lists only the other language", async () => {
    const user = userEvent.setup();
    setup();
    await user.click(screen.getByRole("radio", { name: "Translate + proofread" }));
    const options = () =>
      Array.from((screen.getByLabelText("To") as HTMLSelectElement).options).map((o) => o.value);
    expect(screen.getByLabelText("From")).toHaveValue("en-GB");
    expect(options()).toEqual(["de-DE", "de-AT", "de-CH"]);
    expect(screen.getByLabelText("To")).toHaveValue("de-DE");
    await user.selectOptions(screen.getByLabelText("From"), "de-AT");
    expect(options()).toEqual(["en-US", "en-GB"]);
    expect(screen.getByLabelText("To")).toHaveValue("en-GB");
    // A target of the other language survives a change of source variant.
    await user.selectOptions(screen.getByLabelText("To"), "en-US");
    await user.selectOptions(screen.getByLabelText("From"), "de-CH");
    expect(screen.getByLabelText("To")).toHaveValue("en-US");
  });

  describe("a PDF", () => {
    const pdf = () => new File(["x"], "Scan.pdf");

    it("shows the OCR options only for a .pdf", async () => {
      const user = userEvent.setup();
      setup();
      await choose(user);
      expect(screen.queryByText("OCR for scanned pages")).not.toBeInTheDocument();
      await choose(user, pdf());
      expect(screen.getByText("OCR for scanned pages")).toBeInTheDocument();
      expect(screen.getByRole("radio", { name: "AI model" })).toBeChecked();
      expect(screen.getByRole("radio", { name: "Tesseract" })).not.toBeChecked();
      expect(screen.getByRole("checkbox", { name: "Re-OCR scanned pages" })).toBeChecked();
    });

    it("posts ocr_engine and reocr", async () => {
      const user = userEvent.setup();
      const { f } = setup();
      await choose(user, pdf());
      await user.click(screen.getByRole("radio", { name: "Tesseract" }));
      await user.click(screen.getByRole("checkbox", { name: "Re-OCR scanned pages" }));
      await user.click(screen.getByRole("button", { name: "Create project" }));
      await waitFor(() => expect(callsTo(f, "POST /v1/books")).toHaveLength(1));
      const body = callsTo(f, "POST /v1/books")[0][1]!.body as FormData;
      expect(body.get("ocr_engine")).toBe("tesseract");
      expect(body.get("reocr")).toBe("false");
    });

    it("posts the defaults, AI and re-OCR, as they are shown", async () => {
      const user = userEvent.setup();
      const { f } = setup();
      await choose(user, pdf());
      await user.click(screen.getByRole("button", { name: "Create project" }));
      await waitFor(() => expect(callsTo(f, "POST /v1/books")).toHaveLength(1));
      const body = callsTo(f, "POST /v1/books")[0][1]!.body as FormData;
      expect(body.get("ocr_engine")).toBe("ai");
      expect(body.get("reocr")).toBe("true");
    });

    it("sends neither field for a DOCX", async () => {
      const user = userEvent.setup();
      const { f } = setup();
      await choose(user);
      await user.click(screen.getByRole("button", { name: "Create project" }));
      await waitFor(() => expect(callsTo(f, "POST /v1/books")).toHaveLength(1));
      const body = callsTo(f, "POST /v1/books")[0][1]!.body as FormData;
      expect(body.has("ocr_engine")).toBe(false);
      expect(body.has("reocr")).toBe(false);
    });

    it("disables the AI model for a local only book", () => {
      renderApp(
        <PdfOptions engine="tesseract" onEngine={() => {}} reocr onReocr={() => {}} localOnly />,
      );
      expect(screen.getByRole("radio", { name: "AI model" })).toBeDisabled();
      expect(screen.getByRole("radio", { name: "Tesseract" })).toBeChecked();
      expect(screen.getByText("Local only books use Tesseract")).toBeInTheDocument();
    });
  });
});
