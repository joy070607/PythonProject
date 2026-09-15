from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from .exceptions import (
    BookNotFoundError,
    DuplicateBookError,
    LoanNotFoundError,
)
from .models import Book, BookStatus, Loan
from .repository import LibraryRepository


class LibraryService:
    def __init__(self, repository: LibraryRepository) -> None:
        self.repository = repository

    def register_book(self, isbn: str, title: str, author: str) -> Book:
        if self.repository.find_book(isbn.strip()) is not None:
            raise DuplicateBookError(f"이미 등록된 ISBN입니다: {isbn}")
        book = Book(isbn=isbn, title=title, author=author)
        self.repository.add_book(book)
        return book

    def search_books(self, keyword: str) -> list[Book]:
        normalized = keyword.strip().casefold()
        if not normalized:
            return self.repository.list_books()
        return [
            book
            for book in self.repository.list_books()
            if normalized in book.isbn.casefold()
            or normalized in book.title.casefold()
            or normalized in book.author.casefold()
        ]

    def available_books(self) -> list[Book]:
        return [
            book
            for book in self.repository.list_books()
            if book.status is BookStatus.AVAILABLE
        ]

    def borrow_book(self, isbn: str, borrower: str) -> Loan:
        book = self._require_book(isbn)
        borrower = borrower.strip()
        if not borrower:
            raise ValueError("대출자 이름은 비어 있을 수 없습니다.")
        book.borrow()
        loan = Loan(
            loan_id=uuid4().hex[:12],
            book_isbn=book.isbn,
            borrower=borrower,
            borrowed_at=datetime.now(UTC),
        )
        self.repository.save_book(book)
        self.repository.add_loan(loan)
        return loan

    def return_book(self, loan_id: str) -> Loan:
        loan = self.repository.find_loan(loan_id)
        if loan is None:
            raise LoanNotFoundError(f"대출 기록을 찾을 수 없습니다: {loan_id}")
        book = self._require_book(loan.book_isbn)
        loan.close()
        book.return_copy()
        self.repository.save_loan(loan)
        self.repository.save_book(book)
        return loan

    def list_loans(self, active_only: bool = False) -> list[Loan]:
        loans = self.repository.list_loans()
        return [loan for loan in loans if loan.is_active] if active_only else loans

    def _require_book(self, isbn: str) -> Book:
        book = self.repository.find_book(isbn.strip())
        if book is None:
            raise BookNotFoundError(f"도서를 찾을 수 없습니다: {isbn}")
        return book

