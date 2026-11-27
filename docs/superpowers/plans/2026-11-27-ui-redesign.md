# UI Redesign (paper, ink and gilt) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the whole Jerome web app one modern, bookish identity (warm palette, Inter plus EB Garamond, a gilt accent) and CSS-only micro-animations, without changing what any page does or says.

**Architecture:** New theme tokens, fonts and a motion layer in `web/src/index.css`; restyled shared primitives in `web/src/components/ui/`; a few small shared components (`Skeleton`, `Loading`, `Logo`); then page-level touches. No JavaScript animation library; motion is CSS classes and keyframes, switched off under `prefers-reduced-motion`.

**Tech Stack:** React 19, Tailwind CSS 4 (`@theme inline`), shadcn/ui primitives on radix-ui, vitest + Testing Library, Vite 7.

**Spec:** `docs/superpowers/specs/2026-11-27-ui-redesign-design.md`

## Global Constraints

- **No visible copy, role or accessible-name changes.** Every existing web test passes unchanged; only tests for new behaviour are added.
- **New dependencies:** `tw-animate-css` (dev) and `@fontsource-variable/inter` only. Install with `npm install` in `web/`; nothing else, and nothing with brew.
- **No requests to outside hosts at run time.** Inter is bundled; EB Garamond comes from Jerome's own `/fonts/`.
- **Palette values** are the OKLCH values in the spec's table. `brand` and `brand-foreground` are new tokens (`--color-brand`, `--color-brand-foreground`). `ring` is the brand colour with alpha 0.55. `destructive` and `ai` do not change.
- **Gilt is an accent, never body text on paper.** Primary buttons stay `primary` (ink).
- **Motion tokens:** `--ease-out: cubic-bezier(0.22, 1, 0.36, 1)`; `--dur-fast: 120ms`, `--dur-base: 180ms`, `--dur-page: 240ms`.
- **Reduced motion:** one `@media (prefers-reduced-motion: reduce)` block in `index.css` sets `animation-duration: 0.01ms`, `animation-iteration-count: 1`, `transition-duration: 0.01ms` and `scroll-behavior: auto` on `*, *::before, *::after`.
- **Nothing a person does often waits on an animation:** no animation delays input, and none runs longer than 300 ms except the looping shimmer and pulse.
- **Page state is never reset by an animation:** no React `key` is added to restart one.
- **Public repository:** no book files and no owner-specific names in code, docs or assets.
- **Style:** existing code style (Prettier-like, long JSX lines allowed). Commits end with `Co-Authored-By: Claude Fable 5.2 <noreply@anthropic.com>`.
- **Test commands** (in `web/`): `npx vitest run --testTimeout=30000 <files>` and `npx tsc -p tsconfig.app.json --noEmit`. This Mac times out under load: rerun alone any file that fails only in a full run.

## Review Focus

1. A person with "reduce motion" on: no page may move, pulse or shimmer. Pinned in Task 1 (`theme.test.ts` checks the block) and checked by hand in the browser.
2. Changing chapter on Review or Preview: the page-enter animation must not replay and no state may reset. Pinned in Task 3 (`routeKey` tests).
3. Dark theme: every restyled surface must be readable (gilt on dark, muted text on cards). Checked in the browser in the final step; contrast pinned in Task 1 for `muted-foreground`.
4. A list refetching every few seconds (projects with a running job): rows must not flicker or replay the stagger. Pinned in Task 4 (rows keep their keys; the test rerenders with new data).
5. Loading states for screen readers: each skeleton announces "Loading…" once through `role="status"`. Pinned in Task 2.

---

### Task 1: Foundations: tokens, fonts, motion layer, favicon

**Files:**
- Modify: `web/package.json` (the two dependencies), `web/src/index.css`, `web/src/main.tsx` (import the Inter font CSS), `web/index.html` (favicon link), `web/src/components/preview/book.css` (remove the `@font-face` rules, now in `index.css`)
- Create: `web/public/favicon.svg`, `web/src/theme.test.ts`

**Interfaces:**
- Produces, for all later tasks:
  - colour utilities `bg-brand`, `text-brand`, `border-brand`, `text-brand-foreground` and `ring-ring` in the new values;
  - font utilities `font-sans` (Inter Variable) and `font-display` (EB Garamond);
  - animation classes in `index.css`:
    - `page-enter`: fade in and rise 6 px over `--dur-page`;
    - `row-enter`: fade in and rise 4 px over `--dur-base`, delayed by `calc(var(--i, 0) * 25ms)`;
    - `card-enter`: fade in and rise 4 px over `--dur-fast`;
    - `shimmer`: a light band sweeping left to right, 1.6 s, infinite, as a `::after` overlay;
    - `pulse-dot`: opacity and scale pulse, 1.4 s, infinite;
    - `skeleton`: `bg-muted` with a slow opacity pulse, 1.4 s, infinite;
    - `pressable`: `transition` on transform, `active:scale-[0.97]`.

- [ ] **Step 1: Write the failing test** `web/src/theme.test.ts`. It reads `src/index.css` and `index.html` with `node:fs` and asserts:
  - `index.css` imports `tw-animate-css`;
  - it defines `--brand` and `--brand-foreground` under both `:root` and `.dark`, and maps `--color-brand`, `--font-display` and `--font-sans` in `@theme inline`;
  - `:root` has `--background: oklch(0.985 0.006 85)` and `.dark` has `--background: oklch(0.17 0.008 70)`;
  - it contains `@media (prefers-reduced-motion: reduce)` with `animation-duration: 0.01ms`;
  - it defines `@keyframes` named `page-enter`, `row-enter`, `shimmer` and `pulse-dot`;
  - it declares `@font-face` for `"EB Garamond"` four times, and `book.css` declares none;
  - `index.html` links `/favicon.svg` with `rel="icon"`;
  - **contrast:** a small helper converts the light and dark `muted-foreground` and `background` OKLCH values to relative luminance and asserts a ratio of at least 4.5.

- [ ] **Step 2: Run** `npx vitest run src/theme.test.ts`. Expected: FAIL (no import, no tokens).

- [ ] **Step 3: Implement.**
  - `npm install -D tw-animate-css` and `npm install @fontsource-variable/inter`.
  - `main.tsx`: `import "@fontsource-variable/inter";` before `./index.css`.
  - `index.css`: `@import "tw-animate-css";` after Tailwind; the token values from the spec; `--font-sans: "Inter Variable", ui-sans-serif, system-ui, sans-serif` and `--font-display: "EB Garamond", ui-serif, Georgia, serif`; `body` gets `font-sans`, `font-feature-settings: "cv11", "ss01"` and a 200 ms colour transition for the theme switch; the keyframes and classes listed under Interfaces; the reduced-motion block.
  - `favicon.svg`: a 32×32 rounded square in the gilt colour `#C99A2E` with a serif "J" in `#2A2520`.

- [ ] **Step 4: Run** `npx vitest run src/theme.test.ts src/components/preview` and `tsc`. Expected: PASS and clean.

- [ ] **Step 5: Commit** `feat: the paper, ink and gilt theme, fonts and the motion layer`.

### Task 2: Shared primitives and components

**Files:**
- Modify: `web/src/components/ui/button.tsx`, `card.tsx`, `table.tsx`, `progress.tsx`, `input.tsx`, `badge.tsx`, `tabs.tsx`; `web/src/components/StageBadge.tsx`; `web/src/components/DropZone.tsx`
- Create: `web/src/components/ui/skeleton.tsx`, `web/src/components/Loading.tsx`, `web/src/components/Logo.tsx`
- Test: `web/src/components/shared-ui.test.tsx`

**Interfaces:**
- Consumes: the classes and utilities from Task 1.
- Produces:
  - `Skeleton({ className }): JSX.Element` — a `div` with `aria-hidden` and the `skeleton` class;
  - `Loading({ children, className }): JSX.Element` — a `div role="status"` holding `<span className="sr-only">Loading…</span>` and the skeleton `children` (default: three `Skeleton` lines);
  - `Logo({ wordmark = true }): JSX.Element` — the gilt square with a serif "J" (`aria-hidden`), followed by "Jerome" in `font-display` when `wordmark` is true;
  - `Progress` gains `running?: boolean`: when true the root has `data-running="true"` and the indicator the `shimmer` class;
  - `StageBadge` renders `<span data-running-dot aria-hidden>` with the `pulse-dot` class before the label for the stages `importing`, `translating` and `proofreading`, and for no other stage.

- [ ] **Step 1: Write the failing tests** in `shared-ui.test.tsx`:
  - `Loading` has `role="status"`, contains the text "Loading…" once, and its skeletons are `aria-hidden`;
  - `Loading` with custom children renders them;
  - `Progress running` sets `data-running="true"`; without the prop the attribute is absent;
  - `StageBadge` shows the running dot for `importing`, `translating` and `proofreading` and not for `set_up`, `translated`, `needs_attention`, `to_review` and `done`; the label text is unchanged for all eight;
  - `Logo` shows "Jerome"; with `wordmark={false}` it shows no visible text and the mark is `aria-hidden`.

- [ ] **Step 2: Run** `npx vitest run src/components/shared-ui.test.tsx`. Expected: FAIL (modules missing).

- [ ] **Step 3: Implement the components and restyle the primitives.**
  - `button`: add `pressable`; the default variant keeps `bg-primary`; focus ring uses `ring-ring`.
  - `card`: `shadow-xs`; `table`: rows get `transition-colors hover:bg-muted/50`, head text `text-xs uppercase tracking-wide text-muted-foreground`; `input`: focus ring in the brand colour; `tabs`: the active trigger gets a 150 ms transition.
  - `progress`: track `bg-muted`, indicator `bg-brand` with a 300 ms transform transition.
  - `DropZone`: while a file is dragged over, the border is `border-brand`, the background `bg-brand/10`, and the zone scales to 1.01 (150 ms).

- [ ] **Step 4: Run** the new test, then every test under `src/components`, then `tsc`. Expected: PASS and clean.

- [ ] **Step 5: Commit** `feat: shared pieces of the redesign: skeleton, loading, logo, running states`.

### Task 3: The shell: logo, navigation marker, page enter

**Files:**
- Modify: `web/src/components/AppShell.tsx`
- Create: `web/src/lib/pageEnter.ts`
- Test: `web/src/lib/pageEnter.test.ts`, plus cases in `web/src/components/AppShell.test.tsx`

**Interfaces:**
- Consumes: `Logo` (Task 2), `page-enter` (Task 1).
- Produces:
  - `routeKey(pathname: string): string` — the pathname, with the chapter id removed for `/projects/:id/review/:chapterId` and `/projects/:id/preview/:chapterId`;
  - `usePageEnter(key: string): RefObject<HTMLDivElement | null>` — a ref for the content wrapper; when `key` changes (not on the first render) it removes the `page-enter` class, reads `offsetWidth`, and adds the class again, in a layout effect.

- [ ] **Step 1: Write the failing tests.**
  - `routeKey("/projects/7/review/3")` is `"/projects/7/review"`; `routeKey("/projects/7/preview/12")` is `"/projects/7/preview"`; `routeKey("/projects/7")`, `routeKey("/")` and `routeKey("/settings")` are unchanged; `routeKey("/projects/7/review")` is unchanged.
  - AppShell: the sidebar shows the logo; the routed content sits in an element with `data-page` and the class `page-enter`; navigating from `/projects/7/review/3` to `/projects/7/review/4` keeps the same wrapper element and its `data-route-key`; navigating to `/activity` changes `data-route-key` to `/activity` while the wrapper element stays the same node (no remount).
  - The active navigation link has `data-active="true"`; the others do not.

- [ ] **Step 2: Run** `npx vitest run src/lib/pageEnter.test.ts src/components/AppShell.test.tsx`. Expected: the new cases FAIL.

- [ ] **Step 3: Implement.**
  - The sidebar header uses `Logo` (the mark alone below `lg`, mark and wordmark from `lg` up), keeping the link's `title="Jerome"` and its accessible name.
  - `NavItem`: `data-active`; a gilt bar on the left of the active item (an absolutely placed `span`, `aria-hidden`, scaling in over 180 ms); the icon moves 1 px right on hover.
  - `<Outlet />` is wrapped in `<div ref={…} data-page data-route-key={key} className="page-enter min-w-0 flex-1">`.

- [ ] **Step 4: Run** the two files and `tsc`. Expected: PASS and clean.

- [ ] **Step 5: Commit** `feat: the shell gets the logo, an active marker and a page-enter animation`.

### Task 4: Pages: titles, loading, empty states, lists

**Files:**
- Modify: `web/src/pages/LoginPage.tsx`, `ChangePasswordPage.tsx`, `ProjectsPage.tsx`, `ProjectPage.tsx`, `ActivityPage.tsx`, `ReviewPage.tsx`, `PreviewPage.tsx`, `TeamPage.tsx`, `SettingsPage.tsx`, `FeedbackPage.tsx`, `NewProjectPage.tsx`; `web/src/components/RunningJobsStrip.tsx`; `web/src/components/project/ProgressPanel.tsx`, `AttentionPanel.tsx`, `PublicationSection.tsx`
- Test: `web/src/pages/redesign.test.tsx`

**Interfaces:**
- Consumes: `Loading`, `Skeleton`, `Logo`, `Progress running`, `row-enter`, `font-display`.
- Produces: nothing for later tasks.

- [ ] **Step 1: Write the failing tests** in `redesign.test.tsx` (mock the API with `mockApi`):
  - **Login:** shows the logo and the line "Proofreading and translation for books"; the form's labels and the "Sign in" button are unchanged.
  - **Projects, loading:** while `/v1/projects` is pending, a `role="status"` element with "Loading…" is shown.
  - **Projects, list:** each of the first 12 rows has the `row-enter` class and `--i` equal to its index; a 13th row has no `row-enter`; rerendering with refetched data keeps the same row elements (same DOM nodes for the same project ids).
  - **Projects, empty:** "No projects yet" and the "+ New project" link are still there.
  - **Projects, running:** the progress bar of a running job has `data-running="true"`.
  - **Titles:** the `h1` of Projects and of a Project page has the `font-display` class.

- [ ] **Step 2: Run** `npx vitest run src/pages/redesign.test.tsx`. Expected: FAIL.

- [ ] **Step 3: Implement.**
  - Every page `h1`: `font-display text-3xl font-normal tracking-tight` in place of `text-2xl font-semibold`.
  - Login and Change password: `Logo` above the card, the tagline under it on Login only, `page-enter` on the wrapper.
  - Projects: a search icon inside the field (`SearchIcon`, `aria-hidden`); titles in `font-display text-base`; the first 12 rows get `row-enter` and `style={{ "--i": index }}`; the empty state gets an icon (`BookOpenIcon`, `aria-hidden`) above the existing text and button; loading is `Loading` with five row-shaped skeletons.
  - Every bare "Loading…" paragraph listed in the spec becomes `Loading` with skeletons shaped like what follows (rows for lists, a title and two blocks for the Project page, paragraph lines for Review and Preview). The Preview's "Load more" button label does not change.
  - `RunningJobsStrip` and `ProgressPanel`: `running` on the progress bars of jobs that are running; book titles in `font-display`.
  - Project page: the title in `font-display`.

- [ ] **Step 4: Run** every test under `src/pages` and `src/components`, then `tsc`. Expected: PASS and clean.

- [ ] **Step 5: Commit** `feat: pages in the new identity: display titles, skeleton loading, livelier lists`.

### Task 5: Review and Preview details

**Files:**
- Modify: `web/src/components/review/FocusCard.tsx`, `Paragraph.tsx`; `web/src/components/preview/PreviewToolbar.tsx`
- Test: cases in `web/src/components/review/FocusCard.test.tsx` and `web/src/pages/PreviewPage.test.tsx`

**Interfaces:**
- Consumes: `card-enter` (Task 1).
- Produces: nothing.

- [ ] **Step 1: Write the failing tests.**
  - FocusCard: the card element has `data-suggestion` equal to the suggestion id and the `card-enter` class; rendering it with another suggestion keeps the same card node (no remount) and updates `data-suggestion`.
  - Preview toolbar: the selected toggle of each group has `data-on="true"`.

- [ ] **Step 2: Run** the two files. Expected: the new cases FAIL.

- [ ] **Step 3: Implement.**
  - FocusCard restarts `card-enter` when the suggestion id changes, the same way `usePageEnter` does (reuse the hook with the id as the key).
  - Suggestion marks in `Paragraph` get `transition-colors duration-150`.
  - Preview toolbar toggles: `data-on`, a 120 ms background transition, and the selected one a gilt underline (`shadow-[inset_0_-2px_0_var(--brand)]`).

- [ ] **Step 4: Run** the whole web suite and `tsc`, then `npm run build`. Expected: all pass, the build succeeds (the `/fonts/…` "didn't resolve at build time" notes are expected).

- [ ] **Step 5: Commit** `feat: quiet motion in the review card and the preview toolbar`.
