# Book preview: the original, the translation and the proofread text

## Why

The owner wants to check a book at each stage: the file as it was uploaded, the text after
translation, and the text after proofreading. Checking means two things. One is reading what
changed, to catch a bad translation or a wrong correction. The other is seeing how the
book reads as a book. Today the original file is kept on disk but cannot be downloaded or
opened, and the only view of a book is the review screen, which is built for deciding marks,
not for reading or comparing.

## Goals

- **Download original** on the project page: the file exactly as it was uploaded. For a
  translation this is the source book's file. For a PDF import, both the PDF and the DOCX
  made from it can be downloaded.
- A **Preview** page per book, chapter by chapter, that shows any of the book's states:
  - **Original**: for a translation, the source book's text. For a book that is only
    proofread, the imported text before any correction.
  - **Translation** (translated books only): the translated text before any correction.
  - **Proofread**: the text with every accepted and edited suggestion applied. It is the
    same text the `docx-clean` / `epub-clean` export writes. Open and rejected suggestions
    are not applied.
- Two **views**:
  - **Text**: plain reading text with the corrections visible. In the Proofread column,
    text the proofreading removed is struck through in red and text it added is
    highlighted green. A "Show changes" switch turns this off.
  - **Book**: the text set as a book page, with:
    - EB Garamond, the fonts Jerome already ships for Make PDF;
    - the column width of the book's trim size;
    - justified text with hyphenation in the book's language;
    - headings, italic, bold, small caps, superscript and subscript, underline and links;
    - quotes indented and list items with their bullet;
    - footnotes collected at the end of the chapter, numbered.
- Two **layouts**:
  - **Single**: one state, full width.
  - **Side by side**: two or three states in columns. Each row is one paragraph, so a
    paragraph and its translation and its proofread text always stand level with each
    other and scroll together. This holds in both views.
- The state, view and layout picked are kept in the address (query string), so a link opens
  the same preview. The browser also remembers the last choice per viewer.

## Non-goals (this version)

- Editing or deciding suggestions in the preview. It is read-only; the review screen stays
  the place for decisions.
- Real page breaks, page numbers and running heads (a real PDF of each state). It can come
  later as "Make preview PDF" on the existing renderer.
- Rendering the original file with Word's own layout (LibreOffice). It is too heavy for the
  image and too slow.
- Pictures. In the Book view a picture or other non-text object (the U+FFFC placeholder in
  a paragraph) shows as a small labelled box, "Picture". Showing the real image is a later
  step.
- History: comparing to a state from last week, or to a single person's decisions.

## Anti-goals

- The preview must never change the book or its suggestions. It only reads.
- The Proofread state must match the clean export. If the preview shows a text that the
  export would not write, the preview is wrong.
- It must stay fast on a long chapter. The data is loaded a page of paragraphs at a time,
  like the review screen.

## Approaches

- **A. A book page drawn in the browser from Jerome's paragraphs.** Chosen. It is instant
  for all three states, it can be laid out side by side with the rows level, and it needs
  no new service.
- **B. A real PDF of each state** from the Make PDF renderer, shown in the browser. It gives
  true pages, but it takes minutes per state on a large book and two PDFs cannot scroll
  together. It stays as a later "Make preview PDF".
- **C. LibreOffice converting the files to PDF.** This is closest to Word, but it adds
  about 500 MB to the image, it is slow, and it does not scroll together either. Rejected.

The owner picked A for the Book view, together with the Text view, both with Single and
Side by side ("hajde obe opcije i single view i side by side", then "go").

## How it works

### Data

Every paragraph (block) already holds what is needed:
- `text` and `marks` (formatting spans);
- for a translation, `source_block_id`, which points to the source paragraph and its text
  and marks;
- suggestions, whose accepted and edited ones give the proofread text through
  `text_with_changes`.

`chapter_review` already returns `source`, `original` and `current` texts per block for the
review screen. The preview needs the same states plus the formatting.

New endpoint:

- `GET /v1/chapters/{chapter_id}/preview?offset=&limit=`: the same paging as
  `/chapters/{id}/review`.
- It answers with the chapter's id, title and `total_blocks`, and for each block:
  - `id`, `ordinal` and `kind`;
  - `original`: `{text, marks}` from the source block for a translation, else the block's
    own text and marks;
  - `translation`: `{text, marks, status}` for a translation, else null;
  - `proofread`: `{text, marks, changes}`. `changes` is the list of applied changes as
    `{start, end, removed}` on the proofread text, enough to draw the struck and inserted
    spans.
- Marks on the proofread text follow the changes:
  - a mark before a change keeps its place;
  - a mark after a change shifts by the change's length difference;
  - text inserted inside a marked span takes that mark, and text inserted at a mark's edge
    does not;
  - a mark entirely inside removed text disappears.
- **Paragraphs translated into nothing:** a block of the translation whose source block is
  missing (front matter translated on its own) has `original: null`. Its row shows an empty
  cell in the Original column.
- **Access:** anyone who can open the review screen (any signed-in member, API keys too).
- Book-level metadata the page needs (title, whether it is a translation, its language and
  trim size, chapter list) already comes from `/v1/books/{id}` and `/v1/books/{id}/chapters`.

Download:

- `GET /v1/books/{book_id}/original`: the uploaded file, as it was. For a translated book it
  serves the source book's file. It sends the file's name, `<book title>.<ext>`, made safe
  for a header, and its media type.
- `GET /v1/books/{book_id}/original?pdf=true`: the PDF of a PDF import, the kept
  `source.pdf`. It answers 404 when the book was not imported from a PDF.
- **The hidden source book:** the original of a translated book is always reached through
  the translated book. A request for the hidden source book itself gets the same 409 that
  its other routes give.

### Web

- **The Project page:**
  - a **Preview** button next to Review;
  - a **Download original** link, plus **Download original PDF** for a PDF import.
- **Route:** `/projects/:id/preview/:chapterId?`, which opens the first chapter when none
  is given.
- **The toolbar:**
  - the chapter selector, the same as on the review screen;
  - **View**: Text or Book;
  - **Layout**: Single or Side by side;
  - **States**: one choice in Single and two or three in Side by side, with Original,
    Translation and Proofread offered for a translation and Original and Proofread for a
    book that is only proofread;
  - **Show changes**, Text view only.
- **Defaults:**
  - for a translation: Side by side, with Original and Proofread, in the Text view;
  - for a book that is only proofread: Side by side, with Original and Proofread, in the
    Text view.
- **The grid:** one row per paragraph and one column per state shown, with a sticky column
  header that names the state. In Single the column is centered and as wide as the trim
  size in the Book view.
- **The Book view styling:**
  - EB Garamond is served to the web app from the same font files as the PDF renderer;
  - `hyphens: auto` with the paragraph's language: the source language for the Original
    column and the target language for the others;
  - a first paragraph after a heading is not indented, and following paragraphs are;
  - footnote blocks (`kind = footnote`) are taken out of the flow and listed under the
    chapter, numbered in order.
- **Paging:** more paragraphs load as the reader scrolls near the end ("Load more" as the
  fallback), as on the review screen.
- **Empty states:**
  - a chapter not yet translated shows the Translation column as "Not translated yet";
  - a translation that is still running shows the paragraphs done so far.

## Calls I made (the owner can overrule)

- **Pictures** are a labelled box in this version, not the real image.
- **The default** is Side by side, with Original and Proofread, in the Text view.
- **Footnotes** go at the end of the chapter in the Book view, not at the foot of a page,
  since there are no pages. Footnote blocks are listed in their order. An inline reference
  is not linked to its note in this version; it shows as the text carries it.
- **The file name** of a downloaded original is `<title>.<ext>`:
  - the title is the visible book's title, the translated title for a translation;
  - the extension is the stored `original.*` file's.

## Answers to the builder check

- **Marks of a translated block.** The translation pass decodes the formatting tags back
  into marks and stores them on the translated block with its text, so `translation.marks`
  come straight from the block. The source marks come from the source block through a join
  on `source_block_id`. `source_texts` gives text only, so the join adds the marks.
- **"Proofread = the clean export".** The proofread text is the block's base text with
  exactly the changes the clean export records applied by the same function:
  - only accepted and edited suggestions, never open or rejected ones;
  - the base is the block text, which for a translation is the translated text;
  - the function is `recorded_changes` then `apply_changes`.

  The test compares the preview's text with the text those functions produce for the same
  block. It does not parse a written DOCX.
- **A chapter that is not proofread** (its `proofread` flag is off). The Proofread column
  still shows the text with whatever changes are accepted, under a header note "Not
  proofread yet". The note says the export leaves this chapter's corrections out.
- **Languages.** The endpoint returns the variants: `source_variant` from the source book's
  style sheet, for a translation only, and `variant` from the book's own. The web app uses
  them for `lang` and hyphenation.
- **Rows.** A translated book has one block per source block (made at import). The rows
  follow the translated book's blocks, and the paging offset is on them. A translated block
  whose source block is gone shows an empty Original cell. Nothing is shown for a source
  block without a translated block; that cannot happen with today's import.
- **The original PDF of a translated PDF import.** It is served from whichever of the two
  books (the visible one or its hidden source) holds `source.pdf`. The answer is 404 when
  neither does.

## Left to the builder

- The exact layout and component split.
- Debouncing and the scroll-loading threshold.
- How the language is passed for hyphenation.
- How the fonts are served: copied into the web build or served by the API.

## Tests that must exist

- **Backend:**
  - the preview's three states for a translated block with accepted, edited, rejected and
    open suggestions;
  - the proofread text equals the clean export's text for the same block;
  - marks follow changes in each case above;
  - a block with no source;
  - paging;
  - download of the original for a DOCX book, a translated book (the source file), and a
    PDF import (both files);
  - the 409 for the hidden source book;
  - 401 without sign-in.
- **Web:**
  - each view and layout renders the right columns for a translation and for a book that
    is only proofread;
  - the struck and inserted spans, and Show changes off;
  - the choice is kept in the address;
  - footnotes listed at the end in the Book view;
  - the Picture box;
  - the download links on the Project page.
