"""Books — which book a title means (Open Library, free, no key) and its critical reviews
(Exa). The book twin of `ko tv`: domain knowledge lives here, `exa` stays a generic search.

Reviews, measured 2026-11-15 (WORKLOG): on Exa's `auto` type the query wording does the
work ("…book review": 6 of 6 results were reviews, the bare title 1 of 6); the objective
alone changed nothing there, but it steers deep types and costs nothing to send.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ko.exa import ExaResult

OPEN_LIBRARY = "https://openlibrary.org"
DEFAULT_REVIEWS = 5

REVIEWS_OBJECTIVE = (
    "Find substantive critical reviews of this book. Rank first: reviews in established "
    "publications (newspapers, literary magazines, review journals like Kirkus or Publishers "
    "Weekly) and long-form reviews by serious critics or readers. Exclude retailer and publisher "
    "pages, Google Books, Wikipedia, plot-summary or study-guide sites, and author interviews. "
    "Pull the reviewer's overall verdict, the main praise, the main criticism, and the "
    "publication and date."
)


@dataclass
class Book:
    title: str
    authors: list[str] = field(default_factory=list)
    year: int | None = None
    key: str = ""  # Open Library work key, e.g. /works/OL28738217W

    @property
    def url(self) -> str:
        return f"{OPEN_LIBRARY}{self.key}" if self.key else ""


def find(query: str) -> Book | None:
    """The best Open Library match for a title (optionally with the author in it), or None."""
    import httpx2 as httpx

    resp = httpx.get(
        f"{OPEN_LIBRARY}/search.json",
        params={"q": query, "limit": 1, "fields": "title,author_name,first_publish_year,key"},
        timeout=15,
        follow_redirects=True,
    )
    resp.raise_for_status()
    docs = resp.json().get("docs") or []
    if not docs:
        return None
    d = docs[0]
    return Book(d.get("title") or query, d.get("author_name") or [], d.get("first_publish_year"), d.get("key") or "")


def _core(title: str) -> str:
    t = title.lower().strip()
    return t.removeprefix("the ").removeprefix("a ").removeprefix("an ")


def matches(query: str, book: Book) -> bool:
    """Is Open Library's top hit plausibly the book asked for? Its title (less a leading
    article) must appear in the query; "The Wager" matches "Wager", "The Wagered Wife" doesn't."""
    return bool(book.title) and _core(book.title) in query.lower()


def reviews_query(query: str, book: Book | None) -> str:
    """`The Wager` + David Grann -> `The Wager David Grann book review`. The words as typed
    stay (Open Library drops a leading "The"); the author is added only when it isn't there
    and the match is plausibly the right book, since a wrong author steers Exa away."""
    author = book.authors[0] if book and book.authors and matches(query, book) else ""
    if author and author.lower() not in query.lower():
        query = f"{query} {author}"
    return f"{query} book review"


def reviews(query: str, book: Book | None, n: int = DEFAULT_REVIEWS) -> list[ExaResult]:
    """Critical reviews of a book via Exa: highlights per review, query + objective as measured."""
    from ko import exa

    return exa.search(reviews_query(query, book), n=n, objective=REVIEWS_OBJECTIVE)
