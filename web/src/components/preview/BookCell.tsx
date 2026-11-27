import type { ReactNode } from "react";
import { cpToUtf16 } from "@/lib/offsets";
import type { MarkJson, PreviewBlock, PreviewState } from "@/lib/preview";
import { cn } from "@/lib/utils";
import "./book.css";

const PLACEHOLDER = "￼";

/** Outermost first: a link wraps bold, bold wraps italic, and so on. */
const ORDER = ["link", "bold", "italic", "underline", "smallcaps", "superscript", "subscript", "strike"];

function wrap(kind: string, href: string | null, key: string, child: ReactNode): ReactNode {
  switch (kind) {
    case "link":
      return href ? <a key={key} href={href} target="_blank" rel="noreferrer">{child}</a> : child;
    case "bold":
      return <strong key={key}>{child}</strong>;
    case "italic":
      return <em key={key}>{child}</em>;
    case "underline":
      return <u key={key}>{child}</u>;
    case "smallcaps":
      return <span key={key} className="small-caps">{child}</span>;
    case "superscript":
      return <sup key={key}>{child}</sup>;
    case "subscript":
      return <sub key={key}>{child}</sub>;
    case "strike":
      return <s key={key}>{child}</s>;
    default:
      return child;
  }
}

/** The text cut at every mark edge and picture, each piece wrapped in the marks over it. */
export function formatted(text: string, cpMarks: MarkJson[]): ReactNode[] {
  // The API counts code points; JavaScript slices UTF-16 units.
  const marks = cpMarks.map((m) => ({ ...m, start: cpToUtf16(text, m.start), end: cpToUtf16(text, m.end) }));
  const cuts = new Set([0, text.length]);
  for (const m of marks) {
    cuts.add(Math.max(0, Math.min(text.length, m.start)));
    cuts.add(Math.max(0, Math.min(text.length, m.end)));
  }
  for (let i = text.indexOf(PLACEHOLDER); i !== -1; i = text.indexOf(PLACEHOLDER, i + 1)) {
    cuts.add(i);
    cuts.add(i + 1);
  }
  const points = [...cuts].sort((a, b) => a - b);
  const out: ReactNode[] = [];
  for (let i = 0; i + 1 < points.length; i++) {
    const [a, b] = [points[i], points[i + 1]];
    const piece = text.slice(a, b);
    let node: ReactNode =
      piece === PLACEHOLDER ? (
        <span role="img" aria-label="Picture" className="book-picture">Picture</span>
      ) : (
        piece
      );
    const over = marks
      .filter((m) => m.start <= a && m.end >= b)
      .sort((x, y) => ORDER.indexOf(y.kind) - ORDER.indexOf(x.kind)); // innermost first
    over.forEach((m, j) => {
      node = wrap(m.kind, m.href, `${a}-${j}`, node);
    });
    out.push(<span key={a}>{node}</span>);
  }
  return out;
}

/** One paragraph in the Book view, set in the book's typeface. Footnotes are list items,
 * for the Notes list under the chapter. */
export default function BookCell({ block, state, lang, firstAfterHeading, note }: {
  block: PreviewBlock; state: PreviewState; lang: string | undefined; firstAfterHeading: boolean;
  /** A short remark shown before a footnote's text ("Not translated yet"). */
  note?: string;
}) {
  const text = block[state];
  const content = text ? formatted(text.text, text.marks) : null;
  let body: ReactNode;
  switch (block.kind) {
    case "heading":
      body = <h2>{content}</h2>;
      break;
    case "quote":
      body = <blockquote><p>{content}</p></blockquote>;
      break;
    case "list_item":
      body = <ul><li><p>{content}</p></li></ul>;
      break;
    case "footnote":
      return (
        <li lang={lang}>
          {note && <span className="mr-1 text-xs italic opacity-70">{note}</span>}
          {content}
        </li>
      );
    default:
      body = <p className={cn(firstAfterHeading && "no-indent")}>{content}</p>;
  }
  return <div lang={lang} className="book">{body}</div>;
}
