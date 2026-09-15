# 고급파이썬프로그래밍 1차 과제 - 스팀(Steam) 웹 크롤링

## 1. 대상 사이트 및 선정 이유
- 메인 대상: https://store.steampowered.com (스팀 상점)
- 보조 대상: 스팀 공식 Store API(`/api/appdetails`, `/appreviews/{appid}`), https://steamdb.info
- 선정 이유: 게임 개발자 진로를 고려해 대형 게임 플랫폼인 스팀의 게임·이용자 데이터를 수집·분석하여
  최신 게임 트렌드, 개발 엔진/언어, 장르별 인기도 등을 파악하고자 해당 주제를 선정하였다.
- 최종 답변 질문: **해당 데이터 분석으로 게임의 최신 트렌드 경향 분석이 가능한가?**

## 2. 수집 항목
| 구분 | 필드 | 출처 |
|---|---|---|
| 식별/기본 | appid, title, released, detail_url | 검색 결과 페이지 (`a.search_result_row`) |
| 가격 | price_final, price_original, is_discounted | 검색 결과 페이지 (`data-price-final` 속성 우선, 텍스트 파싱 폴백) |
| 분류 | tags, genres, categories | 상세 페이지(`a.app_tag`) / appdetails API |
| 출시/개발 | release_date_detail, developer | 상세 페이지(`div.date`, 개발자 라벨) |
| PC 사양 | min_requirements, recommended_requirements | 상세 페이지 시스템 요구사항 블록 |
| 평가 요약 | review_summary_text, review_positive_pct, review_total_count | `div.user_reviews_summary_row`의 `data-tooltip-html` |
| 집계 지표 | recommendations_total | 공식 Store API `appdetails` → `recommendations.total` |
| 리뷰 텍스트 | sample_reviews(review, voted_up, playtime) | 공식 리뷰 API `appreviews/{appid}` |
| 외부 통계(시도) | concurrent_players_live/24h/alltime, owner_estimates | SteamDB 차트 페이지 (아래 5번 한계 참고) |
| 메타 | source_page_url, crawled_at_utc | 모든 레코드 공통 |

## 3. 설치 및 실행
```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt # requirements.txt에 따라 요구되는 사양의 패키지를 인스톨.

# Chrome 브라우저가 설치되어 있어야 하며, Selenium 4.6+는 Selenium Manager가
# 자동으로 맞는 chromedriver를 내려받는다. 사내/오프라인 환경이라면
# webdriver-manager 패키지를 추가로 사용해도 된다.

python assign.py
```
- 결과 JSON: `data/steam_games.json`
- 실행 로그: `logs/crawl.log`

## 4. 코드 구조 (has-a 방식)
`SteamCrawlPipeline` 이 아래 컴포넌트를 **소유(has-a)** 하여 조립한다.
- `RateLimiter` : 요청 간 1.2초 이상 지연 보장 : 안정적 접근을 위한 딜레이 유발
- `CircuitBreaker` : 특정 대상에서 403/429 발생 시 해당 대상 요청을 즉시 중단 : 예외 처리
- `HttpClient` : timeout, 상태코드 확인, 예외 처리를 포함한 requests 래퍼 : 클라이언트 상태 검증
- `SeleniumBrowser` / `PageFetcher` : **동적 페이지 접근(Selenium), 초기화 실패 시 HTTP 요청으로 자동 대체**
- `SteamStoreCrawler` : 검색 결과·상세 페이지·공식 API 크롤링
- `SteamDBCrawler` : SteamDB 외부 통계 크롤링 (has-a `HttpClient`)
- `DataValidator` : pandas 기반 중복·결측·자료형 검증 : 데이터 검증
- `JsonStorage` : 최종 결과 JSON 저장
- (상세한 코드 분석 및 리뷰를 위해 주석을 비교적 많이 사용함)

## 5. 선택자 근거 및 검증
과제 계획서의 선택자를 실제 라이브 페이지에 직접 HTTP 요청을 보내 검증하였다.
- `a.search_result_row`, `span.title`, `div.search_released`, `a.app_tag`,
  `div.date`, `div.user_reviews_summary_row[data-tooltip-html]` : 현재도 유효함을 확인.
- 가격: `div.discount_final_price`/`discount_original_price` 텍스트보다 상위 래퍼(판다스 라이브러리로 전처리한 데이터를 데코레이터로 후처리 작업을 거쳤기 때문)
  `div.search_price_discount_combined`의 `data-price-final`(센트 단위 정수) 속성이
  더 안정적이어서 이를 우선적으로 사용하도록 보정함.
- **`div.developer > a`** : 2024년 이후 재설계된 상점 페이지(예: CS2)에서는 존재하지 않음.
  "개발자" 라벨의 `div.grid_label` 옆 `div.grid_content > a` 구조로 대체됨을 확인해
  두 구조를 모두 지원하도록 폴백 로직을 추가함. : `div.grid_label` 로직이 정상 작동하지 않을 시 폴백 로직이 작동하는 구조
- **`div.game_area_sys_req_leftCol` / `rightCol`** : 마찬가지로 구형 페이지에서만 존재하며,
  신형 페이지는 OS 탭(`div.game_area_sys_req[data-os="win"]`) 안의 단일 블록에 "최소:"/"권장:"
  텍스트로만 구간이 나뉘어 있음을 확인해 텍스트 분리 로직을 추가함. : 사양 관련 선택자
- 집계 지표는 `recommendations.total` 필드명 그대로 `appdetails` API에서 제공됨.

## 6. 예외 처리
`DataValidator` 클래스에 명시된 기준을 따른다.
1. `appid` 또는 `title` 이 없는 레코드는 분석 불가로 판단해 제거한다.
2. `appid` 기준 중복 레코드는 마지막(가장 정보가 많이 채워진) 레코드만 남긴다.
3. 가격/리뷰/동접자 등 숫자 필드는 `pandas.to_numeric(errors="coerce")` 로 강제 변환하고,
   변환 실패 값은 결측(None)으로 통일해 자료형 혼입을 방지한다.
4. 검증 결과(원본/중복제거/결측제거/최종 건수)는 `data/steam_games.json`의 `meta.validation_report`에 함께 기록한다.

## 7. 한계 및 문제점
- **SteamDB 외부 통계(동시접속자, 소유자 추정치)**: `steamdb.info/robots.txt` 확인 결과 및
  실제 요청 테스트에서 일반 User-Agent 요청이 차단되는 것을 확인함(봇 차단 정책으로 추정).
  이에 따라 `concurrent_players_*`, `owner_estimates` 필드는 대부분 `None` 으로 남을 수 있다.
  이는 접근통제 우회를 시도하지 않는다는 원칙(3번)을 지키기 위한 의도된 동작이며,
  분석 시 해당 필드는 결측치로 처리해야 한다.
- 사용자 리뷰는 `appreviews` API 응답 중 최근 순 일부(기본 5건)만 샘플링한다.
- 상점 페이지 마크업은 게임마다(구형/신형 UI) 다를 수 있어, 위 폴백 로직으로 다수 케이스를
  커버했으나 일부 특수 페이지는 필드가 비어 있을 수 있다.

## 8. AI 활용 내역
- 클로드 프로(Claude Pro)를 활용하여 골격 코드를 기반으로 함수 및 클래스에 따른 로직을 작성함.
- README의 일부 기반을 클로드 프로를 활용하여 작성함.