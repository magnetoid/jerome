# Finished PDF and EPUB

## Intent

After proofreading or translation, the owner sells the book as an e-book. Today Jerome writes
the reviewed text back into the uploaded file: a clean DOCX for a Word manuscript, a clean
EPUB for an EPUB. It cannot make a PDF at all, and it cannot make an EPUB from a Word
manuscript. Making those files needs another tool and another pass by hand.

This project makes the files that go on sale: a finished EPUB and a finished PDF for every
book, Word-born or EPUB-born, proofread or translated. Shop publishing is the next project,
and it will take these files as they are.

## Goals

- Two new export formats, `epub-book` and `pdf-book`, for DOCX and EPUB books, including
  translated books.
- A finished EPUB that works in the big shops (Apple, Kobo, Google, Amazon via EPUB). A
  Word-born EPUB passes EPUBCheck with no errors. An EPUB-born one adds no errors to the
  book's own (see EPUB output).
- A finished PDF for digital sale. It has:
  - a book page size;
  - embedded fonts;
  - a title page, a copyright page and a table of contents with page numbers;
  - running heads and page numbers;
  - footnotes at the foot of the page, and the images;
  - hyphenation for English and German;
  - PDF bookmarks and document metadata.
- Per-book publication details used by both files: author, publisher, ISBNs, year, rights
  line, description and cover image.

## Non-goals

- A print-ready PDF (PDF/X, bleed, CMYK, crop marks). The PDF is for digital sale. Print can
  be a later project.
- Cover design. Jerome takes a finished cover image.
- A template editor or several templates. There is one house template with a few options.
- MOBI, KPF or fixed-layout EPUB.
- Rebuilding an EPUB-born book's EPUB. Its publisher design stays (see EPUB output).

## Anti-goals

- **Changing the text.** Both files contain exactly the reviewed text of the clean export,
  with nothing reflowed into different words, dropped or duplicated. A finished file that
  differs from what was reviewed is a failure, even if it looks good.
- **Fetching anything over the network while rendering.** A manuscript may reference remote
  images or styles. Rendering must never reach a URL (SSRF, tracking, slowness).
- **Book files in the repository.** The repository is public. Tests use only the public
  Gutenberg fixtures in `tests/fixtures/public/` and the DOCX/EPUB test builders. Generated
  files are never committed.

## Constraints

- **Unchanged rule:** export is refused for a source book, and for a translated book while
  any paragraph is pending or flagged. A proofread book is not refused today, and the new
  formats keep that. Instead the job warns when suggestions are still open or chapters are
  not proofread (my call: the owner decides when a book is ready).
- **Fonts:** open licence (SIL OFL), shipped in the Docker image, never downloaded at
  runtime.
- **Image size:** the Docker image may grow by about 100 MB (WeasyPrint's Pango/HarfBuzz
  libraries and the fonts).
- **Stack:** Python 3.12, FastAPI, SQLAlchemy and Alembic (next migration is 0009), the
  React web app, CLI, MCP tools.

## Approaches

The owner picked **A**.

- **A. Jerome builds both files; WeasyPrint lays out the PDF (picked).**
  - Jerome turns the clean export into HTML.
  - From that HTML it writes the EPUB 3 itself, and WeasyPrint renders the PDF with a print
    stylesheet (CSS paged media).
  - One source and one look, all under Jerome's control.
  - Cost: system libraries in the image, and WeasyPrint's speed on long books.
- **B. Pandoc for the EPUB, Typst for the PDF.**
  - The best PDF typography.
  - Less control over what Pandoc does with Jerome's content, and two templates to keep in
    step.
- **C. LibreOffice and Calibre.**
  - Heavy tools that are hard to control.
  - The PDF looks like a printed Word file, not a book.

**Within A, the source is the clean export, not Jerome's blocks.** Blocks hold the text and
its marks only. Images, heading levels, list numbering, table layout and where footnotes are
referenced live only in the file. The clean DOCX/EPUB is the original file with only the
reviewed text changed, so it has all of that, and its text is exactly what was reviewed.

## Design

### 1. Publication details

New per-book fields, in migration 0009:

| Field | Notes |
| --- | --- |
| `author` | Required for either export. One or more names, in display order. |
| `translator` | Optional. Credited on the title page and as `dc:contributor` (role `trl`). |
| `publisher` | Optional. |
| `isbn_epub`, `isbn_pdf` | Optional. Validated as ISBN-13 (checksum); hyphens allowed and kept for display. |
| `year` | Optional. |
| `rights` | Optional. The copyright line. Default `© {year} {author}`. |
| `description` | Optional. Goes into the EPUB metadata. |
| `cover` | Optional. A JPEG or PNG in storage, at least 1400 px on the long side. |
| `trim_size` | One of `6x9in` (default), `5.5x8.5in`, `5x8in`, `a5`. |

- **Title:** the existing `title`, or `translated_title` for a translated book.
- **Language:** from the book's style-sheet variant.
- **Without a cover:** both exports work. The web page warns that the shops require one.
- **Where the values come from (my call):**
  - A newly imported book is prefilled from the file's metadata (OPF `dc:creator`,
    `dc:publisher`, `dc:date`; DOCX `docProps/core.xml` creator).
  - Existing books start empty, and the page asks for the author.
  - A translated book copies author, publisher, rights, description and cover from its
    source book when it is created. The ISBNs are never copied: a translation is another
    book.
- **Editing:** a "Publication" section on the project page, an API to read and update the
  fields and upload the cover, and the same through MCP.

### 2. From the clean export to HTML (`jerome/publish/`)

The export first runs the existing clean export (`docx-clean` or `epub-clean`), then reads
that file into one shared form:
- chapters, each with a title and XHTML body content;
- footnotes, with the place each one is referenced;
- images, as bytes with their media type;
- the language.

- **DOCX:**
  - Use `mammoth` (BSD licence) on the clean DOCX. A style map gives headings by outline
    level, title, quote and list styles.
  - Split chapters exactly where Jerome already splits them: outline level 0, else Jerome's
    parts (the chapters table). The chapter titles are the same ones the project page shows.
  - Footnotes become note references and notes.
  - Embedded images are kept. Linked (external) images are dropped and listed in a warning.
- **EPUB:**
  - Read the clean EPUB's spine documents in order. Chapters and titles follow the book's
    navigation.
  - The book's own CSS is not used for the PDF. Jerome's print stylesheet styles the semantic
    markup: headings, emphasis, superscript, lists, tables, images, block quotes.
  - Note references marked `epub:type="noteref"` (or linked asides) become PDF footnotes.
- **The manuscript's own front pages (my call, see the open question):**
  - Jerome makes the title, copyright and contents pages, so the source's versions are left
    out:
    - **EPUB:** spine documents the book marks as cover, title page, copyright page or
      contents (landmarks, `epub:type` or the EPUB 2 guide);
    - **DOCX:** the Word TOC field.
  - Everything else before the first chapter stays, untitled, after the copyright page and
    not in the contents. That covers a dedication, an epigraph, a foreword without a
    heading.
  - What was left out is listed in the job's warnings.
- **Checking the text (my call on the rules):**
  - Before either file is written, the text of the shared HTML is compared with the text of
    the clean export's blocks, chapter by chapter. The comparison collapses whitespace,
    strips soft hyphens, and leaves out what Jerome adds: generated list and note numbers,
    and the parts left out above.
  - Rendering does not change the text, so the check runs on the HTML only, not on the
    final EPUB or PDF.
  - A difference is a hard stop with no "export anyway". The error names the chapter and
    shows the first differing words.
  - If a real manuscript fails because the converter drops something (a text box, say), the
    converter gets fixed. That is the anti-goal working.

### 3. EPUB output

- **DOCX books:** Jerome writes a new EPUB 3 containing:
  - the OPF, with `dc:title`, `dc:creator`, `dc:language`, `dc:identifier` (the EPUB ISBN as
    `urn:isbn:…`, else a stable `urn:uuid` per book), `dc:publisher`, `dc:date`,
    `dc:description`, `dcterms:modified` and `dc:rights`;
  - `nav.xhtml`, plus `toc.ncx` for older readers;
  - a cover page and cover image (when set), a title page and a copyright page;
  - one XHTML file per chapter;
  - footnotes as EPUB 3 notes (`epub:type="noteref"` and `footnote`, as asides at the end of
    the chapter);
  - images, and Jerome's ebook stylesheet. No embedded fonts: readers use their own.
- **EPUB books:** the output is the existing `epub-clean` file, with the publisher's design
  kept.
  - When publication details are set, the OPF metadata is updated: identifier, creator,
    publisher, date, description and rights. Everything else stays byte for byte, as
    `epub_writer` already does.
  - An uploaded cover replaces the book's cover image item, or is added as one with the
    `cover-image` property when the book has none.
- **Identifier (my call):**
  - `isbn_epub` when set.
  - Otherwise, a proofread EPUB-born book keeps its own `dc:identifier`.
  - A translated book always gets its own `urn:uuid`, never the source's, so shops do not
    take it for the same book.
- **Check (my call):**
  - No repair layer.
  - Tests run EPUBCheck when `epubcheck` is installed, and skip otherwise.
  - A Word-born EPUB must have zero errors.
  - An EPUB-born one must have no errors the input did not have.
  - In production, when EPUBCheck is present, its errors go on the job as warnings.

### 4. PDF output

WeasyPrint renders the HTML with Jerome's print stylesheet:

- **Page:** the book's trim size, with margins suited to it (inner and outer equal, because
  this is a digital PDF).
- **Type:**
  - body in EB Garamond, about 11 pt, line height about 1.35;
  - justified text, hyphenated by the `lang` of the book (WeasyPrint uses Pyphen);
  - widows and orphans 2.
- **Front matter:** title page, copyright page (rights, publisher, PDF ISBN, year), and a
  table of contents whose page numbers come from `target-counter`.
- **Chapters:** each starts on a new page. Running heads: the book title on left pages and
  the chapter title on right pages, none on a chapter's first page. Page numbers at the foot.
- **Footnotes:** `float: footnote`, numbered per chapter.
- **Images:** scaled to fit the text block. Tables use the text width.
- **PDF data:** PDF bookmarks from the chapter headings. Document metadata (title, author,
  language) from the publication details. Fonts embedded as subsets.
- **No network:** WeasyPrint's `url_fetcher` serves only the files of the render, from
  memory or the render's temporary directory. Any other URL is refused, and the missing
  resource is reported in a warning.

### 5. Running it

- Rendering a long book can take tens of seconds. The proxy in front of the app may time
  out a long request, so **the finished files are made by the worker, as a job of type
  `publish`** that produces the asked-for file.
  - The job costs no model money. It shows on the project page with its state and, when
    done, a download link.
  - The existing export storage keeps the file.
  - Warnings (a dropped external image, no cover, left-out front pages, open suggestions)
  go on the job.
- **Limits (my call, to be measured in the plan):**
  - A 600-page book with images should render in under 5 minutes and under 2 GB of memory.
  - The job stops after 15 minutes.
  - A book with more than 200 MB of images is refused, with a message.
  - The plan measures the largest public fixture first and adjusts the numbers if they are
    far off.
- The API, CLI (`jerome export --format pdf-book`) and MCP (`export_book`) start the job.
  - The CLI may wait for it.
  - MCP returns the job, then the `download_url` once it is done, as `export_book` does now.

### 6. Tests

- **Inputs:** the public Gutenberg fixtures (EPUB) and DOCX built by the test builders, with
  headings, lists, a table, footnotes and an image.
- **PDF checks (with `pypdf`, dev only):**
  - page size;
  - the text extracts in order;
  - the fonts are embedded;
  - the bookmarks match the chapters;
  - the metadata is set;
  - a footnote lands on the page of its reference;
  - hyphenation is on for `de`.
- **EPUB checks:** the OPF metadata, the nav, the notes, the cover, and EPUBCheck when
  installed.
- **Guards:**
  - the text check fails on a doctored HTML;
  - a remote URL is never fetched (respx and a stub fetcher).

## Left to the builder

- The CSS details within the numbers above.
- The mammoth style map.
- How the `publish` job fits the existing job and task tables.
- The exact API shapes.
- How the warnings are shown.
- Version pins.
- Whether WeasyPrint's system libraries come from Debian packages or another source in the
  Dockerfile.
