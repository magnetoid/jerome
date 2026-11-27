# UI redesign: paper, ink and gilt

## Intent

Jerome works, but it looks like an unstyled component kit: the stock grey theme, the system
font, no brand, and no motion (dialogs and menus carry animation classes, but the package
that defines them was never installed). The owner wants the whole web app to look and feel
like a current, well-made tool, with small animations, while staying fast and clear for long
sessions of reading and deciding.

The owner chose the direction **"Modern + bookish"**: a clean modern interface with the
character of a tool for books.

## Goals

- One visual identity across every page: palette, type, shape, motion.
- Motion that explains what happened (something opened, loaded, changed, is running), never
  decoration that makes a person wait.
- Better feedback: loading shows the shape of what is coming, running work looks alive,
  empty pages say what to do next.
- Light and dark themes of equal quality.

## Non-goals

- No change to what any page does, to routes, to the API or to the backend.
- No change to visible copy, labels, roles or accessible names. The 350 web tests find
  elements by them, and people know them.
- No JavaScript animation library, and no page-layout rewrites (the review workflow keeps
  its structure and keyboard shortcuts).
- No new pages.

## Anti-goals

- Motion that delays work: nothing blocks input, and nothing a person does often (accepting
  a suggestion, moving with j/k) waits on an animation.
- A look that fights the text. The review and preview pages are for reading; chrome stays
  quiet there.
- Ignoring `prefers-reduced-motion`.

## Approaches

1. **Tokens and primitives first, CSS-only motion (chosen).** Replace the theme tokens,
   add fonts and a small motion layer in CSS, restyle the shared `ui/` primitives and the
   shell, then touch pages only where they need more than they inherit (lists, loading,
   empty states, login). Most pages change without being edited, tests keep passing, and
   the bundle barely grows.
2. **Add a motion library** (Motion/Framer) for page and layout transitions. Richer
   choreography, but a new runtime dependency of tens of kilobytes, and animated mount and
   unmount complicates the jsdom tests. The motion wanted here does not need it.
3. **Rewrite pages one by one with new layouts.** The biggest visual change and the biggest
   risk: weeks of work across 9,000 lines and many tests, for a product whose layouts are
   already sound.

The owner left the how to the builder; approach 1 is the builder's call.

## The design

### Palette

Tokens stay the shadcn names in `web/src/index.css`, so every component picks them up.
Values are OKLCH. Neutrals are warm (hue about 75–85) instead of slate blue.

| Token | Light | Dark |
| --- | --- | --- |
| `background` (paper) | `oklch(0.985 0.006 85)` | `oklch(0.17 0.008 70)` |
| `foreground` (ink) | `oklch(0.21 0.02 60)` | `oklch(0.95 0.01 85)` |
| `card`, `popover` | `oklch(0.998 0.003 85)` | `oklch(0.21 0.008 70)` |
| `primary` | `oklch(0.24 0.02 60)` | `oklch(0.93 0.012 85)` |
| `primary-foreground` | `oklch(0.985 0.006 85)` | `oklch(0.2 0.01 70)` |
| `secondary`, `muted`, `accent` | `oklch(0.955 0.008 85)` | `oklch(0.26 0.008 70)` |
| `muted-foreground` | `oklch(0.5 0.02 70)` | `oklch(0.72 0.015 80)` |
| `border`, `input` | `oklch(0.9 0.01 85)` | `oklch(1 0 0 / 10%)`, `oklch(1 0 0 / 15%)` |
| `brand` (gilt, new) | `oklch(0.72 0.13 78)` | `oklch(0.8 0.12 82)` |
| `brand-foreground` (new) | `oklch(0.24 0.02 60)` | `oklch(0.2 0.01 70)` |
| `ring` | `brand` at 55% | `brand` at 55% |
| `destructive`, `ai` | unchanged | unchanged |

- **Primary actions are ink**, not gilt. Gilt is the accent: the active navigation marker,
  focus rings, progress bars, the logo mark, the "running" dot, and the selected state of
  toggles. It is never body text on paper (its contrast is too low for text).
- Stage colours keep their meaning (sky for Translated, green for Done, red for Needs
  attention).
- Text contrast is at least 4.5:1 in both themes; check `muted-foreground` on `background`
  and on `muted`.

### Type

- **Interface:** Inter (variable), bundled with the app through `@fontsource-variable/inter`
  (font files only, no request to an outside host). `font-feature-settings: "cv11", "ss01"`;
  numbers in tables and counters use `tabular-nums`.
- **Display:** EB Garamond, already served at `/fonts/` for the Book view. Its `@font-face`
  rules move from `book.css` to `index.css`. A `font-display` utility (`--font-display`)
  sets it. Used for: page titles (`h1`), book and chapter titles where they appear as
  names (project list, project header, running-jobs strip), the login wordmark and the
  sidebar wordmark. Everything else stays in Inter.
- Display titles are one step larger than today (`text-3xl` for `h1`) with normal weight
  and slightly tight tracking; body sizes do not change.

### Shape and depth

- Radius stays `0.625rem`.
- Cards and bordered sections: border plus a very soft shadow (`shadow-xs`); popovers and
  dialogs `shadow-lg`. No gradients, no glass.
- The page background is paper; cards, tables and the sidebar sit on `card` or a slightly
  darker paper so the layers read without heavy borders.

### Motion

All CSS. `tw-animate-css` (a CSS-only dev dependency) makes the existing `animate-in` /
`animate-out` classes of dialogs, menus, alert dialogs and tooltips work. Jerome's own
motion lives in `index.css` as tokens and a few keyframes:

- **Tokens:** `--ease-out: cubic-bezier(0.22, 1, 0.36, 1)`; durations 120 ms (press, hover),
  180 ms (menus, small reveals), 240 ms (pages, dialogs).
- **Overlays:** fade and scale from 96% (dialogs, menus, popovers, tooltips), 180 ms.
- **Page enter:** on a route change the page content fades in and rises 6 px, 240 ms. The
  sidebar does not move.
- **Lists:** on first load the first 12 rows of a list or table fade in with a 25 ms
  stagger. Later refetches do not replay it.
- **Buttons:** press scales to 0.97; hover and focus transitions 120 ms. Interactive cards
  and rows get a hover tint.
- **Navigation:** the active item shows a gilt bar on its left that grows in; icons nudge
  1 px on hover.
- **Progress:** bars animate their width (300 ms) and carry a slow shimmer while the job is
  running. Stage badges of running stages (Importing, Translating, Proofreading) show a
  pulsing gilt dot.
- **Loading:** skeleton blocks shaped like the content replace the bare "Loading…" text.
  The text stays for screen readers (`sr-only`), so tests and assistive tech still find it.
- **Review:** the focus card fades and slides 4 px when the focused suggestion changes
  (120 ms, never delaying keys); a decided mark changes colour over 150 ms.
- **Drop zone:** the border and icon react to a file dragged over it.
- **Theme switch:** colours cross-fade over 200 ms.
- **Reduced motion:** under `prefers-reduced-motion: reduce` every animation and transition
  above is off (durations 0.01 ms, no transforms), shimmer and pulse included.

### Pages that get more than the new tokens

- **Shell:** a logo mark (a serif "J" on a gilt square) with the wordmark in EB Garamond;
  the active-item marker; a favicon of the same mark (`web/public/favicon.svg`).
- **Login and Change password:** the mark and wordmark above the card, one line under it
  ("Proofreading and translation for books"), the card with the new depth.
- **Projects:** titles in the display face; rows with hover; an icon in the search field;
  an empty state with an icon, one sentence and the existing button; skeleton rows while
  loading.
- **Project:** the title in the display face, sections as cards, progress with shimmer.
- **Review and Preview:** inherit tokens and overlay motion; plus the focus-card and
  toolbar details above. Reading text keeps its current size and face.
- **Activity, Team, Settings, Feedback, New project:** inherit; loading skeletons and empty
  states where they show bare text today.

## Constraints

- No visible copy, role or accessible-name changes; the existing web tests must pass
  unchanged, apart from tests for new behaviour.
- New dependencies: `tw-animate-css` and `@fontsource-variable/inter` only.
- No requests to outside hosts at run time (fonts are bundled or served by Jerome).
- Public repository: nothing owner-specific in code, docs or assets.
- Works at the current breakpoints (icon sidebar below `lg`).

## Builder's calls (settled after the builder check)

- **Page enter never remounts a page.** The shell wraps the routed content in one element
  and restarts a CSS animation on it when the route key changes (remove the class, force a
  reflow, add it back, in a layout effect). The route key is the pathname with the chapter
  id removed on Review and Preview. No React `key` is used, so page state is never reset.
  Login and Change password, which sit outside the shell, play the same animation once when
  they mount.
- **Loading text.** No web test looks for "Loading…". Each skeleton keeps the text as
  `sr-only` inside an element with `role="status"`. The "Loading…" label of the Preview's
  Load more button stays as it is.
- **Stagger is per mount.** Rows animate when they mount (a CSS animation with a delay by
  index, first 12 rows only). Rows that stay mounted through a refetch keep their keys and
  do not replay; coming back to a page plays it again. No shared state.
- **Font URLs.** `url("/fonts/…")` stays a run-time URL: the API serves it, Vite proxies it
  in development and leaves it unresolved at build time (a known, harmless build note).
  jsdom never loads fonts.
- **`ring`** is a literal OKLCH value with alpha (the brand colour at 55%), like the dark
  `border`.
- **Favicon.** `web/public/` is created with `favicon.svg`, and `index.html` links it. The
  API already serves files at the top of the built web folder.

## Left to the builder

- Exact skeleton shapes per page, and which lists get the stagger.
- Fine-tuning of the OKLCH values to meet contrast, within the hues named above.
- Whether sections on the Project page become `Card` components or keep their markup with
  the card classes.
