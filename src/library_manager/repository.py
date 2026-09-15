from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Protocol

from .models import Book, BookStatus, Loan


class LibraryRepository(Protocol):
    def add_book(self, book: Book) -> None: ...
    def save_book(self, book: Book) -> None: ...
    def find_book(self, isbn: str) -> Book | None: ...
    def list_books(self) -> list[Book]: ...
    def add_loan(self, loan: Loan) -> None: ...
    def save_loan(self, loan: Loan) -> None: ...
    def find_loan(self, loan_id: str) -> Loan | None: ...
    def list_loans(self) -> list[Loan]: ...


class InMemoryLibraryRepository:
    def __init__(self) -> None:
        self._books: dict[str, Book] = {}
        self._loans: dict[str, Loan] = {}

    def add_book(self, book: Book) -> None:
        self._books[book.isbn] = book

    def save_book(self, book: Book) -> None:
        self._books[book.isbn] = book

    def find_book(self, isbn: str) -> Book | None:
        return self._books.get(isbn)

    def list_books(self) -> list[Book]:
        return list(self._books.values())

    def add_loan(self, loan: Loan) -> None:
        self._loans[loan.loan_id] = loan

    def save_loan(self, loan: Loan) -> None:
        self._loans[loan.loan_id] = loan

    def find_loan(self, loan_id: str) -> Loan | None:
        return self._loans.get(loan_id)

    def list_loans(self) -> list[Loan]:
        return list(self._loans.values())


class JsonLibraryRepository(InMemoryLibraryRepository):
    """Small JSON repository for a single-user course project."""

    def __init__(self, path: str | Path) -> None:
        super().__init__()
        self.path = Path(path)
        self._load()

    def add_book(self, book: Book) -> None:
        super().add_book(book)
        self._save()

    def save_book(self, book: Book) -> None:
        super().save_book(book)
        self._save()

    def add_loan(self, loan: Loan) -> None:
        super().add_loan(loan)
        self._save()

    def save_loan(self, loan: Loan) -> None:
        super().save_loan(loan)
        self._save()

    def _load(self) -> None:
        if not self.path.exists():
            return
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self._books = {
            item["isbn"]: Book(
                isbn=item["isbn"],
                title=item["title"],
                author=item["author"],
                status=BookStatus(item["status"]),
            )
            for item in data.get("books", [])
        }
        self._loans = {
            item["loan_id"]: Loan(
                loan_id=item["loan_id"],
                book_isbn=item["book_isbn"],
                borrower=item["borrower"],
                borrowed_at=datetime.fromisoformat(item["borrowed_at"]),
                returned_at=(
                    datetime.fromisoformat(item["returned_at"])
                    if item.get("returned_at")
                    else None
                ),
            )
            for item in data.get("loans", [])
        }

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "books": [
                {
                    "isbn": book.isbn,
                    "title": book.title,
                    "author": book.author,
                    "status": book.status.value,
                }
                for book in self.list_books()
            ],
            "loans": [
                {
                    "loan_id": loan.loan_id,
                    "book_isbn": loan.book_isbn,
                    "borrower": loan.borrower,
                    "borrowed_at": loan.borrowed_at.isoformat(),
                    "returned_at": loan.returned_at.isoformat() if loan.returned_at else None,
                }
                for loan in self.list_loans()
            ],
        }
        self.path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

