from __future__ import annotations

from library_manager.exceptions import BookUnavailableError
from library_manager.repository import InMemoryLibraryRepository
from library_manager.service import LibraryService


def main() -> None:
    service = LibraryService(InMemoryLibraryRepository())

    first = service.register_book(
        "9788966260959",
        "파이썬 코딩의 기술",
        "브렛 슬라킨",
    )
    service.register_book(
        "9781492056355",
        "Fluent Python",
        "Luciano Ramalho",
    )
    print("등록:", first.title, first.status.value)

    found = service.search_books("python")
    print("검색:", [book.title for book in found])

    loan = service.borrow_book(first.isbn, "student01")
    print("대출:", loan.loan_id, loan.book_isbn, loan.borrower)
    print("도서 상태:", first.status.value)

    try:
        service.borrow_book(first.isbn, "student02")
    except BookUnavailableError as exc:
        print("예상된 오류:", exc)

    service.return_book(loan.loan_id)
    print("반납 후 도서 상태:", first.status.value)
    print("활성 대출 수:", len(service.list_loans(active_only=True)))


if __name__ == "__main__":
    main()

