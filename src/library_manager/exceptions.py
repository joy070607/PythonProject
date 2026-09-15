class LibraryError(Exception):
    """Base class for expected domain errors."""


class InvalidBookError(LibraryError):
    pass


class DuplicateBookError(LibraryError):
    pass


class BookNotFoundError(LibraryError):
    pass


class BookUnavailableError(LibraryError):
    pass


class LoanNotFoundError(LibraryError):
    pass


class AlreadyReturnedError(LibraryError):
    pass

