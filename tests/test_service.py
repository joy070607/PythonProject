from __future__ import annotations

import json

import pytest

from library_manager.exceptions import (
    AlreadyReturnedError,
    BookNotFoundError,
    BookUnavailableError,
    DuplicateBookError,
    InvalidBookError,
    LoanNotFoundError,
)
from library_manager.models import BookStatus
from library_manager.repository import InMemoryLibraryRepository, JsonLibraryRepository
from library_manager.service import LibraryService


@pytest.fixture
def service() -> LibraryService:
    return LibraryService(InMemoryLibraryRepository())


def test_register_book(service: LibraryService) -> None:
    book = service.register_book("1", "Clean Code", "Robert Martin")
    assert book.status is BookStatus.AVAILABLE


def test_blank_book_field_is_rejected(service: LibraryService) -> None:
    with pytest.raises(InvalidBookError):
        service.register_book("1", "", "Author")


def test_duplicate_isbn_is_rejected(service: LibraryService) -> None:
    service.register_book("1", "A", "Writer")
    with pytest.raises(DuplicateBookError):
        service.register_book("1", "B", "Writer")


def test_search_matches_title_case_insensitively(service: LibraryService) -> None:
    service.register_book("1", "Fluent Python", "Luciano")
    assert [book.isbn for book in service.search_books("fluent")] == ["1"]


def test_borrow_changes_book_state(service: LibraryService) -> None:
    book = service.register_book("1", "A", "Writer")
    loan = service.borrow_book("1", "student")
    assert book.status is BookStatus.LOANED
    assert loan.is_active


def test_unknown_book_cannot_be_borrowed(service: LibraryService) -> None:
    with pytest.raises(BookNotFoundError):
        service.borrow_book("missing", "student")


def test_loaned_book_cannot_be_borrowed_twice(service: LibraryService) -> None:
    service.register_book("1", "A", "Writer")
    service.borrow_book("1", "student-1")
    with pytest.raises(BookUnavailableError):
        service.borrow_book("1", "student-2")


def test_blank_borrower_is_rejected(service: LibraryService) -> None:
    service.register_book("1", "A", "Writer")
    with pytest.raises(ValueError):
        service.borrow_book("1", " ")


def test_return_closes_loan_and_restores_book(service: LibraryService) -> None:
    book = service.register_book("1", "A", "Writer")
    loan = service.borrow_book("1", "student")
    returned = service.return_book(loan.loan_id)
    assert returned.is_active is False
    assert book.status is BookStatus.AVAILABLE


def test_unknown_loan_cannot_be_returned(service: LibraryService) -> None:
    with pytest.raises(LoanNotFoundError):
        service.return_book("missing")


def test_same_loan_cannot_be_returned_twice(service: LibraryService) -> None:
    service.register_book("1", "A", "Writer")
    loan = service.borrow_book("1", "student")
    service.return_book(loan.loan_id)
    with pytest.raises(AlreadyReturnedError):
        service.return_book(loan.loan_id)


def test_json_repository_round_trip(tmp_path) -> None:
    path = tmp_path / "library.json"
    first = LibraryService(JsonLibraryRepository(path))
    first.register_book("1", "A", "Writer")
    loan = first.borrow_book("1", "student")

    second = LibraryService(JsonLibraryRepository(path))
    assert second.search_books("A")[0].status is BookStatus.LOANED
    assert second.list_loans()[0].loan_id == loan.loan_id
    assert json.loads(path.read_text(encoding="utf-8"))["books"][0]["isbn"] == "1"

