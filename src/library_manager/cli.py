from __future__ import annotations

import argparse
from collections.abc import Sequence

from .exceptions import LibraryError
from .models import Book, Loan
from .repository import JsonLibraryRepository
from .service import LibraryService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="도서 대출 관리")
    parser.add_argument("--db", default="data/library.json", help="JSON 저장 파일")
    commands = parser.add_subparsers(dest="command", required=True)

    register = commands.add_parser("register", help="도서 등록")
    register.add_argument("isbn")
    register.add_argument("title")
    register.add_argument("author")

    search = commands.add_parser("search", help="도서 검색")
    search.add_argument("keyword")
    commands.add_parser("available", help="대출 가능 목록")

    borrow = commands.add_parser("borrow", help="도서 대출")
    borrow.add_argument("isbn")
    borrow.add_argument("borrower")

    return_command = commands.add_parser("return", help="도서 반납")
    return_command.add_argument("loan_id")

    loans = commands.add_parser("loans", help="대출 기록")
    loans.add_argument("--active", action="store_true")
    return parser


def _book_line(book: Book) -> str:
    return f"{book.isbn} | {book.title} | {book.author} | {book.status.value}"


def _loan_line(loan: Loan) -> str:
    returned = loan.returned_at.isoformat(timespec="seconds") if loan.returned_at else "대출 중"
    return f"{loan.loan_id} | {loan.book_isbn} | {loan.borrower} | {returned}"


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    service = LibraryService(JsonLibraryRepository(args.db))
    try:
        if args.command == "register":
            print(_book_line(service.register_book(args.isbn, args.title, args.author)))
        elif args.command == "search":
            for book in service.search_books(args.keyword):
                print(_book_line(book))
        elif args.command == "available":
            for book in service.available_books():
                print(_book_line(book))
        elif args.command == "borrow":
            print(_loan_line(service.borrow_book(args.isbn, args.borrower)))
        elif args.command == "return":
            print(_loan_line(service.return_book(args.loan_id)))
        elif args.command == "loans":
            for loan in service.list_loans(active_only=args.active):
                print(_loan_line(loan))
    except (LibraryError, ValueError) as exc:
        print(f"오류: {exc}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

