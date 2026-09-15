# 3주차 프로젝트: 도서 대출 관리

## 학습 목표

`dataclass`, 합성, 저장소 추상화, 사용자 정의 예외, 타입 힌트와 `pytest`를 하나의 작은 프로그램으로 연결합니다. CLI는 입력만 해석하고, 업무 규칙은 `LibraryService`가 담당합니다.

## 구조

```text
CLI → LibraryService → Book·Loan
                   ↘ Repository → JSON 파일
```

```text
src/library_manager/
├── cli.py          # 명령행 입력과 출력
├── service.py      # 등록·검색·대출·반납 유스케이스
├── models.py       # Book, Loan, BookStatus
├── repository.py   # Protocol, 메모리·JSON 저장소
└── exceptions.py   # 도메인 오류
```

## 실행

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
python -m pip install -e . pytest

library-manager --db data/library.json register 9788966260959 "파이썬 코딩의 기술" "브렛 슬라킨"
library-manager --db data/library.json available
library-manager --db data/library.json borrow 9788966260959 student01
library-manager --db data/library.json loans --active
library-manager --db data/library.json return <LOAN_ID>
pytest -q
```

## 상세 사용 예시

아래 과정은 빈 `data/library.json`에서 시작하는 하나의 사용 시나리오입니다. 이전 실행 결과를 지우고 싶다면 해당 JSON 파일을 다른 이름으로 옮긴 뒤 시작하십시오.

### 1단계: 도서 등록

```bash
library-manager --db data/library.json register 9788966260959 "파이썬 코딩의 기술" "브렛 슬라킨"
library-manager --db data/library.json register 9781492056355 "Fluent Python" "Luciano Ramalho"
```

예상 출력:

```text
9788966260959 | 파이썬 코딩의 기술 | 브렛 슬라킨 | available
9781492056355 | Fluent Python | Luciano Ramalho | available
```

같은 ISBN을 다시 등록하면 서비스가 중복을 거부합니다.

```bash
library-manager --db data/library.json register 9788966260959 "중복 도서" "작성자"
```

```text
오류: 이미 등록된 ISBN입니다: 9788966260959
```

이 명령의 종료 코드는 `2`입니다. 셸에서는 `echo $?`, PowerShell에서는 `$LASTEXITCODE`로 확인할 수 있습니다.

### 2단계: 검색과 대출 가능 목록

```bash
library-manager --db data/library.json search python
library-manager --db data/library.json available
```

검색어는 ISBN, 제목, 저자에 대소문자 구분 없이 적용됩니다. 빈 검색 결과는 오류가 아니며 아무 행도 출력하지 않습니다.

### 3단계: 도서 대출

```bash
library-manager --db data/library.json borrow 9788966260959 student01
```

예상 출력 형식:

```text
<LOAN_ID> | 9788966260959 | student01 | 대출 중
```

`LOAN_ID`는 실행할 때 생성되는 12자리 값입니다. 예를 들어 `7f2a91c8d314`가 출력되었다면 반납할 때 그 값을 사용합니다.

대출 후 다음 명령으로 현재 상태를 확인합니다.

```bash
library-manager --db data/library.json available
library-manager --db data/library.json loans --active
```

- `available`에는 대출한 도서가 나타나지 않습니다.
- `loans --active`에는 방금 생성한 대출 기록이 나타납니다.

같은 도서를 다시 대출하면 다음 오류가 발생합니다.

```text
오류: 이미 대출 중인 도서입니다: 9788966260959
```

### 4단계: 반납과 기록 확인

```bash
library-manager --db data/library.json return <LOAN_ID>
library-manager --db data/library.json available
library-manager --db data/library.json loans
```

반납 명령의 출력 마지막 항목에는 UTC 기준 반납 시각이 기록됩니다. 반납된 도서는 다시 `available` 목록에 나타납니다. 같은 `LOAN_ID`를 다시 반납하면 `이미 반납 처리된 대출입니다` 오류가 발생합니다.

### 5단계: Python API 직접 사용

```bash
python examples/full_workflow.py
```

이 예제는 메모리 저장소를 사용하므로 파일을 만들지 않습니다. 등록, 검색, 대출, 중복 대출 오류, 반납 상태를 순서대로 출력합니다. CLI와 서비스 계층의 차이를 확인할 때 사용하십시오.

### 6단계: JSON 파일 읽기

`data/library.json`에는 `books`와 `loans` 배열이 저장됩니다.

```json
{
  "books": [
    {
      "isbn": "9788966260959",
      "title": "파이썬 코딩의 기술",
      "author": "브렛 슬라킨",
      "status": "available"
    }
  ],
  "loans": [
    {
      "loan_id": "7f2a91c8d314",
      "book_isbn": "9788966260959",
      "borrower": "student01",
      "borrowed_at": "2026-09-10T03:10:00+00:00",
      "returned_at": "2026-09-10T03:18:00+00:00"
    }
  ]
}
```

식별자와 시각은 실행할 때마다 달라집니다. JSON을 직접 수정하면 도서와 대출 상태가 어긋날 수 있으므로 프로그램 명령으로 상태를 변경하십시오.

## 핵심 규칙

- 같은 ISBN은 두 번 등록할 수 없습니다.
- 대출 중인 책은 다시 대출할 수 없습니다.
- 이미 반납된 대출을 다시 반납할 수 없습니다.
- 상태 변경은 `Book.borrow()`, `Book.return_copy()`, `Loan.close()`에서만 수행합니다.
- 서비스는 오류를 숨기지 않고 구체적인 도메인 예외로 알립니다.

## 스스로 확장하기

1. 회원 모델과 1인당 대출 권수 제한을 추가합니다.
2. 반납 예정일과 연체 여부를 `Loan`에 추가합니다.
3. JSON 저장 중 실패해도 기존 파일이 유지되도록 원자적 저장을 적용합니다.

## 자주 발생하는 문제

| 증상 | 원인 | 확인 방법 |
|---|---|---|
| `library-manager` 명령을 찾지 못함 | 가상환경이 비활성화되었거나 설치하지 않음 | 가상환경 활성화 후 `python -m pip install -e .` |
| 등록한 도서가 보이지 않음 | 서로 다른 `--db` 경로 사용 | 모든 명령에서 같은 JSON 경로 사용 |
| 반납할 수 없음 | ISBN을 반납 번호로 입력 | `loans --active`에서 `LOAN_ID` 확인 |
| 테스트에서 기존 데이터가 섞임 | 실제 JSON 저장소를 테스트에 사용 | `InMemoryLibraryRepository` 또는 `tmp_path` 사용 |

