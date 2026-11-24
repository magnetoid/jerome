"""The shared form both finished files are made from: chapters of XHTML, images, warnings."""

from dataclasses import dataclass, field

from jerome.errors import JeromeError

NOTE_CLASS = "jr-note"  # <span class="jr-note">…</span>: a footnote, at its reference point
# <span class="jr-drop">…</span> inside a note: the source's own note number or back-link,
# kept so the text check sees it, and hidden in the finished files
DROP_CLASS = "jr-drop"


class PublishError(JeromeError):
    """A finished file cannot be made; the publish job fails at once."""


class PublishTextError(PublishError):
    """The finished file's text would differ from the reviewed text."""


@dataclass
class PubImage:
    name: str
    media_type: str
    data: bytes


@dataclass
class PubChapter:
    """``html`` is an XHTML body fragment (h1-h4, p, lists, tables, images, notes). A title
    of None (or empty) is labelled ``Chapter {n}`` in the contents."""

    title: str | None
    html: str
    in_toc: bool = True


@dataclass
class PubBook:
    language: str
    chapters: list[PubChapter]
    images: dict[str, PubImage] = field(default_factory=dict)
    left_out: list[str] = field(default_factory=list)  # the source's own front pages
    warnings: list[str] = field(default_factory=list)


def chapter_labels(pub: PubBook) -> list[str]:
    """Each chapter's contents label: its title, else ``Chapter {n}`` counting the chapters
    in the contents, else "the text before the first chapter"."""
    labels, n = [], 0
    for chapter in pub.chapters:
        n += chapter.in_toc
        title = " ".join((chapter.title or "").split())
        labels.append(title or (f"Chapter {n}" if chapter.in_toc else "the opening pages"))
    return labels
