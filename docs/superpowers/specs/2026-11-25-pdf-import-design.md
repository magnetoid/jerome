# PDF import: text PDFs and scanned PDFs, through DOCX

**Status:** draft for the owner's approval, 2026-11-25. The owner chose approach A.

## Intent

The owner gets books as PDF files and wants Jerome to take them like DOCX and EPUB: import, proofread or translate, then get a PDF back. Some PDFs are typeset with real text and some are scans, so both must work ("i OCR i TEKST"). For scans, both OCR engines must be there ("oboje.. za svaki slucaj"): an AI model and Tesseract.

## Goals

1. **Upload:** a `.pdf` can be uploaded where DOCX and EPUB are today (the New project page, `POST /v1/books`, the CLI).
   - The upload asks for the OCR engine, **AI model** or **Tesseract**.
   - The default is AI model when an AI model is set up in Settings → Models and the book is not `local_only`. Otherwise it is Tesseract.
   - A `local_only` book never sends pages to a cloud model, so it always uses Tesseract.
2. **One reading per page.** Jerome looks at each page by itself.
   - A page with a usable text layer is read from that layer. This is free and needs no OCR.
   - A page without one (a scanned image, or text that does not decode) goes to the chosen OCR engine.
   - A PDF that is partly scanned works.
3. **Rebuilding the text.** From what was read, Jerome builds a clean DOCX:
   - paragraphs are rejoined across lines and across pages;
   - running headers, footers and page numbers are dropped;
   - words split by a hyphen at a line end are rejoined;
   - headings become Word headings, so chapters split as they do for any DOCX;
   - italic and bold are kept where they can be seen;
   - footnotes are kept as notes where they can be told apart (see What's left to the builder).
4. **That DOCX becomes the book's original.**
   - The book is a DOCX book from then on: review, LanguageTool, AI check, translation, the DOCX download with changes, Make PDF and Make EPUB all work unchanged.
   - The original PDF is kept beside it, for reference and to rerun the import.
5. **Import runs in the background, as a job.** OCR of a book takes minutes.
   - The project exists at once, marked "Importing… page 120 of 300".
   - Review and jobs are refused until the import is done.
   - A failed import shows its error, and can be retried or deleted.
6. **Cost:** AI OCR is paid.
   - The import job has an estimate and a `max_cost`, as other jobs do, and model usage is recorded as for every model call.
   - Tesseract and text pages cost nothing.
7. **Languages:** English and German, as for the rest of Jerome. The book's variant (en-US … de-CH) picks the OCR language.

## Non-goals

- Editing the original PDF in place, keeping its layout. The PDF that comes out is Jerome's own finished PDF.
- Tables, columns of text side by side, mathematics, and text inside images. They come out as best effort, in reading order.
- Fraktur and other blackletter type.
- Languages other than English and German.

## Anti-goals

These would make the feature a failure even if it technically works:

- A PDF that hangs or crashes the worker, or exhausts the server's memory. PDFs are untrusted files.
- Silent loss of text: pages dropped without a word, or a scan that returns nothing and is not reported.
- Running headers and page numbers left in the text, so that LanguageTool and the AI mark them on every page.

## Approaches

- **A, chosen: PDF → DOCX, then the ordinary DOCX import.** Everything after the import works as it does now. The DOCX can be opened in Word to fix structure before review.
- **B: a new book format "pdf".** Rejected: every writer (the DOCX download, translation, review exports) would need a "pdf" version.

## Decided

- **Text layer:** read with `pdfminer.six` (MIT). It gives each character's position, font name and size, which are the clues for paragraphs, headings and italics.
- **Rendering scans:** pages are rendered to images with `pypdfium2` (Apache-2.0/BSD) at 300 dpi.
- **Tesseract:** runs as the `tesseract` program, with its TSV output for positions and confidence.
  - The Docker image installs `tesseract-ocr`, `tesseract-ocr-eng` and `tesseract-ocr-deu`.
  - No Python wrapper is needed.
- **AI OCR:** uses the model set for a new pass **ocr** under Settings → Models, chosen like the ai and translate passes.
  - The model must accept images. The Claude and OpenAI-compatible providers (Grok) get an image input on `complete`.
  - The model returns each page as a small structured list of blocks (heading, paragraph, footnote), with italic and bold marked, through the schema mechanism the other passes use.
- **Untrusted PDFs:** parsing and rendering run in a `spawn` child process with a time limit and a memory cap, the same way the finished-PDF render does. A page that fails is reported by page number; the whole import does not stop for it.
- **Limits:**
  - at most 2000 pages;
  - a PDF upload limit of its own, `max_pdf_upload_mb`, defaulting to 200 MB, because scans are large. DOCX and EPUB keep `max_upload_mb`.
- **Storage:** the generated DOCX is saved as the original, named after the PDF with `.docx`. The PDF is saved beside it as `source.pdf`.
- **Book state:** a new `import_status` column on books: `null` (ready), `importing` or `failed`, with `import_error`.
- **The job:** a new job type `import`, with one task kind `import`.
  - Its stats hold `pages`, `pages_done`, `ocr_pages` and `failed_pages`.
  - When it ends, the import report lists the pages that were OCR'd, the pages that failed and the engine used. It shows on the project page.

## Owner's answers (after the builder check)

- **Estimate, then confirm.** The upload makes the project, and a free first step reads the PDF and sorts its pages: text layer, needs OCR, picture only. Text pages and Tesseract pages go ahead at once. When AI OCR is chosen, the import stops at "Estimate ready" with the number of OCR pages and the cost, and no paid page is read until the owner confirms with a `max_cost`, as for other jobs. If the owner turns AI down at that point, Tesseract reads those pages instead.
- **Existing OCR layers.** Scanned pages that already carry an invisible text layer are detected as a full-page image with text over it. The upload has the option "Re-OCR scanned pages", on by default: such pages go through the chosen engine. With it off, their existing layer is used, for free.
- **Keep the pictures.** Illustrations and photos go into the DOCX in their place, so Make PDF and Make EPUB keep them. On a scanned page, a picture region found by the engine is cropped from the page image. A page that is only a picture becomes a picture, not a failed page. Page 1 does not become the cover by itself; the cover is still set under Publication.

## Calls I made (from the builder check)

- **Tesseract as a safety net:** when the AI fails on a page, Tesseract reads that page instead. The AI fails when it refuses, returns nothing, errors after retries, or the job's cost cap is reached. The report says which engine read each page. A page counts as failed only when both engines fail.
- **Rerunning:** this means retrying or resuming a failed import job. Re-importing a book that is already ready, or replacing its DOCX, is out of scope.
- **Failed pages:** they are listed by number in the import report on the project page. The book is ready anyway, with a visible warning. No placeholder text goes into the DOCX, because it would be proofread, translated and published.
- **MCP (Janus):** `import_book` refuses `.pdf` with a clear message for now. PDF import is for the web page, the REST API and the CLI.
- **Long imports:** the import is split into tasks of about 25 pages, so it does not hold the worker for hours. One last task assembles the DOCX.

## Left to the builder

- **The exact rules for paragraph joins, header and footer detection and heading detection.** They must be tested on generated PDFs only; the owner's books are not used on this machine.
  - Pinned: a line that repeats at the same place on most pages, or a lone number at the top or bottom, is a header, footer or page number.
  - Pinned: a heading is a line clearly larger than the body text, or alone and centered.
- **Footnotes:** small text at the bottom of a page, starting with a number that matches a superscript in the page's body, becomes a DOCX footnote. Anything less certain stays as a paragraph at the end of its page. That is safe, and visible in review.
- **The AI OCR prompt and the page schema.**
- **Estimating AI OCR cost:** pages times a per-page token estimate, times the ocr model's price.
- **The web UI:** the "Importing…" state on the projects list and the project page, the engine choice on the New project page, and the import report.
