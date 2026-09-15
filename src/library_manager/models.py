from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum

from .exceptions import AlreadyReturnedError, BookUnavailableError, InvalidBookError


class BookStatus(str, Enum):
    AVAILABLE = "available"
    LOANED = "loaned"


@dataclass(slots=True)
class Book:
    isbn: str
    title: str
    author: str
    status: BookStatus = BookStatus.AVAILABLE

    def __post_init__(self) -> None:
        self.isbn = self.isbn.strip()
        self.title = self.title.strip()
        self.author = self.author.strip()
        if not self.isbn or not self.title or not self.author:
            raise InvalidBookError("ISBN, 제목, 저자는 비어 있을 수 없습니다.")

    def borrow(self) -> None:
        if self.status is BookStatus.LOANED:
            raise BookUnavailableError(f"이미 대출 중인 도서입니다: {self.isbn}")
        self.status = BookStatus.LOANED

    def return_copy(self) -> None:
        if self.status is BookStatus.AVAILABLE:
            raise AlreadyReturnedError(f"이미 반납된 도서입니다: {self.isbn}")
        self.status = BookStatus.AVAILABLE


@dataclass(slots=True)
class Loan:
    loan_id: str
    book_isbn: str
    borrower: str
    borrowed_at: datetime
    returned_at: datetime | None = None

    @property
    def is_active(self) -> bool:
        return self.returned_at is None

    def close(self, returned_at: datetime | None = None) -> None:
        if not self.is_active:
            raise AlreadyReturnedError(f"이미 반납 처리된 대출입니다: {self.loan_id}")
        self.returned_at = returned_at or datetime.now(UTC)

