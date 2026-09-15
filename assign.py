"""
고급파이썬프로그래밍 - 1차 과제: 스팀(Steam) 웹 크롤링

대상 사이트
- store.steampowered.com : 검색 결과/상세 페이지 (동적 렌더링 -> Selenium)
- store.steampowered.com/api, /appreviews : 스팀 공식 Store API (집계 지표, 리뷰 텍스트)
- steamdb.info : 동시 접속자 수, 소유자 추정치 (외부 고도화 통계)

설계 방식
- has-a 구조 기반 객체지향 설계.
  SteamCrawlPipeline 이 RateLimiter / CircuitBreaker / HttpClient / SeleniumBrowser /
  SteamStoreCrawler / SteamDBCrawler / DataValidator / JsonStorage 를 가지고 있으며
  각 컴포넌트를 조립해 전체 크롤링 파이프라인을 수행한다.

필수 요구사항 반영
- 서로 다른 페이지 3쪽 이상 방문 (검색 결과 페이지 페이지네이션 + 상세 페이지 + API 응답)
- 총 50개 이상 레코드 수집 (MIN_RECORDS)
- 분석용 속성 3개 이상 + source_page_url + crawled_at_utc 저장
- timeout, HTTP 상태 확인, 예외 처리, 요청 간 1초 이상 지연 (RateLimiter, HttpClient)
- robots.txt 사전 확인 (check_robots_allowed)
- 403/429 발생 시 해당 대상에 대한 크롤링 즉시 중단 (CircuitBreaker)
- 중복·결측·자료형 점검 기준 명시 (DataValidator 문서화)
- JSON 파일로 저장 (JsonStorage)
"""

from __future__ import annotations

import json
import logging
import math
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin
from urllib.robotparser import RobotFileParser

import pandas as pd
import requests
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait


# ------------------------------------------------------------------
# 0. 공통 설정(유틸리티) : has-a 구조 기반으로 각 컴포넌트에서 재사용
# ------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent # 현재 파일로부터의 상위 디렉토리 경로
DATA_DIR = BASE_DIR / "data" # 데이터 저장 디렉토리
LOG_DIR = BASE_DIR / "logs" # 로그 저장 디렉토리
DATA_DIR.mkdir(exist_ok=True) # 데이터 디렉토리 생성 (이미 존재하면 무시)
LOG_DIR.mkdir(exist_ok=True) # 로그 디렉토리 생성 (이미 존재하면 무시)

logging.basicConfig( # 기본 로거 설정: 파일 + 콘솔 출력
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "crawl.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("steam_crawler") # 로거 인스턴스 생성

# 접근 시 User-Agent 를 명시하지 않으면 스팀이 봇으로 판단해 403/429를 반환하므로 지정하였다.
USER_AGENT = "AdvancedPython-HW1/1.0 (education; contact: jonggyunbag23@gmail.com)"
STORE_HOST = "https://store.steampowered.com" # 스팀 상점 검색/상세 페이지 url
SEARCH_URL = f"{STORE_HOST}/search/" # 스팀 검색 페이지 url
STEAMDB_HOST = "https://steamdb.info" # 스팀 데이터베이스 url

MIN_RECORDS = 50           # 요구사항: 총 50개 이상 레코드
MIN_PAGES = 3               # 요구사항: 서로 다른 페이지 3쪽 이상 방문
REQUEST_DELAY_SEC = 1.2     # 요구사항: 요청 간 1초 이상 지연
SAFETY_MAX_SEARCH_PAGES = 40  # 무한루프 방지 변수


# ------------------------------------------------------------------
# 1. 공통 코드 구조
# ------------------------------------------------------------------
def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_multiline(text: str) -> str:
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def _to_int(text: str | None) -> int | None:
    if not text:
        return None
    digits = re.sub(r"[^\d]", "", text)
    return int(digits) if digits else None


def _split_min_recommended(text: str | None) -> tuple[str | None, str | None]:
    """2024년 이후 재설계된 상점 페이지는 최소/권장 사양이 하나의 블록 안에
    '최소:' / '권장:' 텍스트로만 구분되어 있으므로 해당 구분자를 기준으로 나눈다."""
    if not text:
        return None, None
    marker = re.search(r"권장\s*:", text)
    if not marker:
        return _clean_multiline(text) or None, None
    min_part = _clean_multiline(text[: marker.start()]) or None
    rec_part = _clean_multiline(text[marker.start():]) or None
    return min_part, rec_part


def _parse_review_tooltip(html_fragment: str | None) -> dict[str, Any]:
    """data-tooltip-html 속성(예: '매우 긍정적 (98%의 91,234명 이용자)')을 파싱한다."""
    result: dict[str, Any] = {
        "review_summary_text": None,
        "review_positive_pct": None,
        "review_total_count": None,
    }
    if not html_fragment:
        return result
    text = BeautifulSoup(html_fragment, "html.parser").get_text(" ", strip=True)
    result["review_summary_text"] = text or None

    pct_match = re.search(r"(\d{1,3})%", text)
    if pct_match:
        result["review_positive_pct"] = int(pct_match.group(1))

    count_match = re.search(r"([\d,]+)\s*(개|명|user|review)", text, re.IGNORECASE)
    if count_match:
        result["review_total_count"] = int(count_match.group(1).replace(",", ""))
    return result


def _sanitize_for_json(value: Any) -> Any:
    """DataFrame -> dict 변환 과정에서 생기는 NaN 등 JSON으로 표현할 수 없는 값을
    리스트/딕셔너리 내부까지 재귀적으로 순회하며 None 으로 치환한다.

    pandas.DataFrame.where(pd.notnull(df), None) 은 tags/sample_reviews/
    owner_estimates 처럼 리스트·딕셔너리가 섞인 object 컬럼에서 결측 판정이
    제대로 되지 않아(NaN 이 그대로 남아 JSON에 유효하지 않은 리터럴로 출력됨)
    이 함수로 대체한다.
    """
    if isinstance(value, float) and math.isnan(value):
        return None
    if isinstance(value, dict):
        return {k: _sanitize_for_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitize_for_json(v) for v in value]
    return value


def check_robots_allowed(base_url: str, path: str, user_agent: str = USER_AGENT) -> bool:
    """robots.txt 를 확인해 해당 경로 크롤링이 허용되는지 사전 점검한다."""
    parser = RobotFileParser()
    parser.set_url(urljoin(base_url, "/robots.txt"))
    try:
        parser.read()
    except Exception as exc:  # noqa: BLE001 - robots.txt 접근 실패 시 보수적으로 차단
        logger.warning("robots.txt 확인 실패(%s): 접근을 보수적으로 차단합니다.", exc)
        return False
    allowed = parser.can_fetch(user_agent, path)
    logger.info("robots.txt 확인: %s%s -> %s", base_url, path, "허용" if allowed else "차단")
    return allowed


# ------------------------------------------------------------------
# 2. 공통 부품 (has-a 로 조립될 컴포넌트)
# ------------------------------------------------------------------
class RateLimiter:
    """요청 간 최소 지연시간을 보장한다 (요구사항: 1초 이상)."""

    def __init__(self, delay_sec: float = REQUEST_DELAY_SEC) -> None:
        self.delay_sec = delay_sec
        self._last_call = 0.0

    def wait(self) -> None:
        elapsed = time.monotonic() - self._last_call
        remaining = self.delay_sec - elapsed
        if remaining > 0:
            time.sleep(remaining)
        self._last_call = time.monotonic()


class CircuitBreaker: # 크롤링 접근 오류 발생 시 에러 메시지 반환
    """403/429 응답이 발생하면 해당 대상(target)에 대한 이후 요청을 즉시 중단시킨다."""

    def __init__(self) -> None:
        self.tripped: dict[str, str] = {}

    def trip(self, target: str, reason: str) -> None:
        if target not in self.tripped:
            logger.warning("CircuitBreaker 작동: '%s' 대상 크롤링을 즉시 중단합니다 (%s)", target, reason)
        self.tripped[target] = reason

    def is_tripped(self, target: str) -> bool:
        return target in self.tripped


class HttpClient:
    """requests.Session 을 감싸 timeout·HTTP 상태 확인·예외 처리·지연을 강제한다."""

    def __init__(self, rate_limiter: RateLimiter, breaker: CircuitBreaker) -> None:
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": USER_AGENT,
            "Accept-Language": "ko,en;q=0.8",
        })

        # 연령 확인 쿠키: 로그인/CAPTCHA 우회가 아니라 스팀이 공식적으로
        # 제공하는 "성인 콘텐츠 확인" 화면을 통과하기 위한 표준 공개 절차이다.
        self.session.cookies.update({
            "birthtime": "0",
            "lastagecheckage": "1-January-1970",
            "wants_mature_content": "1",
        })
        self.rate_limiter = rate_limiter  # has-a
        self.breaker = breaker            # has-a

    def get(
        self,
        url: str,
        *,
        target: str,
        params: dict[str, Any] | None = None,
        timeout: tuple[int, int] = (5, 20),
    ) -> requests.Response | None:
        if self.breaker.is_tripped(target):
            return None

        self.rate_limiter.wait()
        try:
            response = self.session.get(url, params=params, timeout=timeout)
        except requests.exceptions.RequestException as exc:
            logger.error("요청 실패 (%s): %s", url, exc)
            return None

        if response.status_code in (403, 429):
            self.breaker.trip(target, f"HTTP {response.status_code}")
            return None
        try:
            response.raise_for_status()
        except requests.exceptions.HTTPError as exc:
            logger.warning("HTTP 오류 (%s): %s", url, exc)
            return None
        return response


class SeleniumBrowser:
    """Selenium 기반 동적 페이지 접근을 캡슐화한다 (스팀 상점은 JS 렌더링이 필요하므로 검색/상세 페이지에만 접근)."""

    def __init__(self, rate_limiter: RateLimiter, headless: bool = True) -> None:
        self.rate_limiter = rate_limiter  # has-a
        options = Options()

        # 페이지 접근 기본 설정
        if headless:
            options.add_argument("--headless=new")

        options.add_argument("--window-size=1366,900")
        options.add_argument(f"user-agent={USER_AGENT}")
        options.add_argument("--disable-gpu")
        options.add_argument("--no-sandbox")
        options.add_argument("--lang=ko-KR")

        self.driver = webdriver.Chrome(options=options) # 크롬 웹드라이버로도 실행 가능
        self.driver.set_page_load_timeout(20)
        self._prime_age_check_cookies()

    def _prime_age_check_cookies(self) -> None:
        """성인 콘텐츠 확인 페이지를 건너뛰기 위한 표준 쿠키를 최초 1회 주입한다.
           => 확인하였다는 의미의 상태 정보 데이터를 쿠키로 전송하여 건너뛰기"""
        try:
            self.driver.get(STORE_HOST)
            for name, value in {
                "birthtime": "0",
                "lastagecheckage": "1-January-1970",
                "wants_mature_content": "1",
            }.items():
                self.driver.add_cookie({"name": name, "value": value, "domain": ".steampowered.com"})
        except WebDriverException as exc:
            logger.warning("초기 쿠키 설정 실패: %s", exc)

    def get_html(self, url: str) -> str | None:
        self.rate_limiter.wait()
        try:
            self.driver.get(url)
            WebDriverWait(self.driver, 15).until(
                lambda d: d.execute_script("return document.readyState") == "complete"
            )
        except TimeoutException:
            logger.warning("페이지 로드 타임아웃: %s", url)
        except WebDriverException as exc:
            logger.error("Selenium 접근 실패 (%s): %s", url, exc)
            return None
        return self.driver.page_source

    def close(self) -> None:
        try:
            self.driver.quit()
        except WebDriverException:
            pass


class PageFetcher:
    """검색/상세 페이지 접근을 담당한다.

    스팀 상점은 동적 웹페이지이므로 기본적으로 Selenium(Chrome) 으로 접근한다.
    단, 실행 환경에 Chrome/chromedriver 가 없어 Selenium 초기화에 실패하면
    (혹은 로드 중 오류가 나면) 일반 HTTP 요청으로 자동 대체(폴백)하여 파이프라인이
    계속 진행되도록 한다. 실제 확인 결과 검색 결과·상세 페이지의 필요한
    데이터는 서버 렌더링 HTML에도 포함되어 있어 HTTP 대체 방식으로도 동일한
    필드를 수집할 수 있다.
    """

    def __init__(self, http: HttpClient, rate_limiter: RateLimiter, use_selenium: bool = True, headless: bool = True) -> None:
        self.http = http                  # has-a: HTTP 폴백 경로
        self.rate_limiter = rate_limiter  # has-a
        self.browser: SeleniumBrowser | None = None
        if use_selenium:
            try:
                self.browser = SeleniumBrowser(rate_limiter, headless=headless)
                logger.info("Selenium(Chrome) 브라우저로 동적 페이지에 접근합니다.")
            except Exception as exc:  # noqa: BLE001 - 드라이버/브라우저 미설치 등 모든 초기화 실패를 포함하는 경우
                logger.warning("Selenium 사용 불가(%s), HTTP 요청 방식으로 대체합니다.", exc)

    def get_html(self, url: str, *, target: str = "store_html") -> str | None:
        if self.browser is not None:
            html = self.browser.get_html(url)
            if html is not None:
                return html
            logger.warning("Selenium 로드 실패, HTTP 요청으로 재시도합니다: %s", url)
        response = self.http.get(url, target=target)
        return response.text if response is not None else None

    def close(self) -> None:
        if self.browser is not None:
            self.browser.close()


# ------------------------------------------------------------------
# 3. 스팀 상점 크롤러 (has-a PageFetcher, HttpClient)
# ------------------------------------------------------------------
class SteamStoreCrawler:
    """검색 결과 목록 + 상세 페이지 + 공식 Store API 를 조합해 게임 레코드를 수집한다."""

    def __init__(self, fetcher: PageFetcher, http: HttpClient) -> None:
        self.fetcher = fetcher  # has-a: 동적/정적 목록·상세 페이지 접근
        self.http = http        # has-a: 공식 JSON API 호출

    # --- 3-1. 검색 결과 페이지 (반복 요소: a.search_result_row) ---
    def crawl_search_pages(self, min_pages: int, min_records: int) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        seen_appids: set[str] = set()
        page = 0

        while page < min_pages or len(records) < min_records:
            start = page * 25
            page_url = f"{SEARCH_URL}?start={start}&count=25&filter=topsellers&cc=kr&l=koreana"
            html = self.fetcher.get_html(page_url, target="store_search")
            if html is None:
                logger.warning("검색 페이지 로드 실패, 수집을 중단합니다: %s", page_url)
                break

            soup = BeautifulSoup(html, "html.parser")
            rows = soup.select("a.search_result_row")
            if not rows:
                logger.info("검색 결과가 더 이상 없어 페이지 수집을 종료합니다 (page=%d)", page)
                break

            for row in rows:
                appid = row.get("data-ds-appid")
                if not appid or appid in seen_appids:
                    continue
                seen_appids.add(appid)
                records.append(self._parse_search_row(row, page_url))

            page += 1
            if page >= SAFETY_MAX_SEARCH_PAGES:
                logger.warning("안전장치 발동: 검색 페이지 %d쪽에서 수집을 중단합니다.", page)
                break

        logger.info("검색 결과 페이지 %d쪽에서 게임 %d건 수집 완료", page, len(records))
        return records

    @staticmethod
    def _clean_price(text: str) -> float | None:
        text = text.strip().replace("\xa0", " ")
        if not text or "무료" in text or text.lower() == "free":
            return 0.0
        match = re.search(r"[\d.,]+", text)
        if not match:
            return None
        number = match.group(0).replace(",", "")
        try:
            return float(number)
        except ValueError:
            return None

    @classmethod
    def _parse_search_row(cls, row, page_url: str) -> dict[str, Any]:
        appid = row.get("data-ds-appid")
        title_el = row.select_one("span.title")
        released_el = row.select_one("div.search_released")

        # 가격은 우선 data-price-final(센트 단위 정수) 속성을 신뢰하고,
        # 없는 경우에만 화면 표시 텍스트를 파싱한다(구형 페이지 대비 폴백).
        price_wrapper_el = row.select_one("div.search_price_discount_combined")
        price_final_el = row.select_one("div.discount_final_price")
        price_original_el = row.select_one("div.discount_original_price")
        price_normal_el = row.select_one("div.game_purchase_price")

        final_price = None
        original_price = None
        is_discounted = price_original_el is not None

        if price_wrapper_el is not None and price_wrapper_el.get("data-price-final") is not None:
            raw = price_wrapper_el.get("data-price-final")
            final_price = int(raw) / 100 if raw.isdigit() else None
        elif price_final_el is not None:
            final_price = cls._clean_price(price_final_el.get_text())
        elif price_normal_el is not None:
            final_price = cls._clean_price(price_normal_el.get_text())

        if price_original_el is not None:
            original_price = cls._clean_price(price_original_el.get_text())

        return {
            "appid": appid,
            "title": title_el.get_text(strip=True) if title_el else None,
            "released": released_el.get_text(strip=True) if released_el else None,
            "price_final": final_price,
            "price_original": original_price,
            "is_discounted": is_discounted,
            "detail_url": row.get("href"),
            "source_page_url": page_url,
            "crawled_at_utc": _utc_now_iso(),
        }

    # --- 3-2. 상세 페이지: 태그/출시일/개발자/PC 사양/리뷰 요약 ---
    def enrich_with_detail_page(self, record: dict[str, Any]) -> None:
        url = record.get("detail_url")
        if not url:
            return
        html = self.fetcher.get_html(url, target="store_detail")
        if html is None:
            return
        soup = BeautifulSoup(html, "html.parser")

        tags = [a.get_text(strip=True) for a in soup.select("a.app_tag")]
        record["tags"] = [t for t in tags if t]

        date_el = soup.select_one("div.date")
        record["release_date_detail"] = date_el.get_text(strip=True) if date_el else None

        # 개발자: 구형 페이지(div.developer > a) 우선 시도 후,
        # 2024년 이후 재설계된 페이지(라벨 "개발자" 옆 grid_content > a)로 폴백한다.
        dev_el = soup.select_one("div.developer a") or soup.select_one("div.dev_row a")
        if dev_el is None:
            for label in soup.select("div.grid_label"):
                if label.get_text(strip=True) == "개발자":
                    content = label.find_next_sibling("div", class_="grid_content")
                    if content is not None:
                        dev_el = content.select_one("a")
                    break
        record["developer"] = dev_el.get_text(strip=True) if dev_el else None

        # PC 사양: 구형 페이지(좌/우 2단 컬럼) 우선 시도 후,
        # 2024년 이후 재설계된 페이지(OS 탭 안의 단일 블록 + '최소:'/'권장:' 텍스트 구분)로 폴백한다.
        min_req_el = soup.select_one("div.game_area_sys_req_leftCol")
        rec_req_el = soup.select_one("div.game_area_sys_req_rightCol")
        if min_req_el is not None or rec_req_el is not None:
            record["min_requirements"] = _clean_multiline(min_req_el.get_text("\n")) if min_req_el else None
            record["recommended_requirements"] = _clean_multiline(rec_req_el.get_text("\n")) if rec_req_el else None
        else:
            win_block = soup.select_one('div.game_area_sys_req[data-os="win"] div.game_area_sys_req_full') \
                or soup.select_one("div.game_area_sys_req_full")
            full_text = win_block.get_text("\n") if win_block else None
            min_text, rec_text = _split_min_recommended(full_text)
            record["min_requirements"] = min_text
            record["recommended_requirements"] = rec_text

        # user_reviews_summary_row 는 실제로는 <a> 태그이며 페이지에 여러 개(최근 평가/
        # 언어별 평가/반응형 중복)가 존재한다. schema.org itemprop="aggregateRating" 이
        # 붙은 것이 스팀이 명시하는 전체 평가 요약이므로 이를 우선적으로 사용한다.
        review_row = soup.select_one('.user_reviews_summary_row[itemprop="aggregateRating"]') \
            or soup.select_one(".user_reviews_summary_row[data-tooltip-html]")
        tooltip_html = review_row.get("data-tooltip-html") if review_row else None
        record["review_summary_raw"] = tooltip_html
        record.update(_parse_review_tooltip(tooltip_html))

        record["source_page_url"] = url
        record["crawled_at_utc"] = _utc_now_iso()

    # --- 3-3. 공식 Store API: appdetails -> recommendations.total 집계 지표 ---
    def fetch_review_aggregate(self, record: dict[str, Any]) -> None:
        appid = record.get("appid")
        if not appid:
            return
        url = f"{STORE_HOST}/api/appdetails"
        params = {"appids": appid, "cc": "kr", "l": "koreana"}
        response = self.http.get(url, target="store_api", params=params)
        if response is None:
            return
        try:
            payload = response.json()
        except ValueError:
            logger.warning("appdetails JSON 파싱 실패: appid=%s", appid)
            return

        entry = payload.get(str(appid), {}) if isinstance(payload, dict) else {}
        if not entry.get("success"):
            return
        data = entry.get("data", {})
        record["recommendations_total"] = data.get("recommendations", {}).get("total")
        record["genres"] = [g.get("description") for g in data.get("genres", []) if g.get("description")] or None
        record["categories"] = [c.get("description") for c in data.get("categories", []) if c.get("description")] or None

    # --- 3-4. 리뷰 전용 API: appreviews -> 텍스트 + 추천 여부 ---
    def fetch_sample_reviews(self, record: dict[str, Any], max_reviews: int = 5) -> None: # 초기화 후 데이터 분석 및 포매팅 진행
        appid = record.get("appid")
        if not appid:
            record["sample_reviews"] = []
            return
        url = f"{STORE_HOST}/appreviews/{appid}"
        params = {
            "json": 1,
            "filter": "recent",
            "language": "koreana",
            "num_per_page": max_reviews,
            "purchase_type": "all",
        }
        response = self.http.get(url, target="store_api", params=params)
        if response is None:
            record["sample_reviews"] = []
            return
        try:
            payload = response.json()
        except ValueError:
            record["sample_reviews"] = []
            return

        reviews = payload.get("reviews", [])[:max_reviews]
        record["sample_reviews"] = [
            {
                "review": (r.get("review") or "").strip(),
                "voted_up": r.get("voted_up"),
                "playtime_forever_min": (r.get("author") or {}).get("playtime_forever"),
            }
            for r in reviews
        ]


# ------------------------------------------------------------------
# 4. SteamDB 크롤러 (has-a HttpClient) - 동시접속자 / 소유자 추정치
# ------------------------------------------------------------------
class SteamDBCrawler:
    """SteamDB 차트 페이지에서 동시접속자 수와 소유자 추정치를 수집한다. 차트 페이지의
    테이블 필드 데이터를 배열 형태로 저장하여 가져온다.

    SteamDB 는 봇 차단(Cloudflare 등)이 적용될 수 있으므로 403/429 발생 시
    CircuitBreaker 로 이후 요청을 즉시 중단하고, 실패한 게임은 관련 필드를
    None 으로 남긴 채 나머지 파이프라인은 계속 진행한다.
    """

    def __init__(self, http: HttpClient) -> None:
        self.http = http  # has-a

    def enrich_with_steamdb(self, record: dict[str, Any]) -> None:
        appid = record.get("appid")
        if not appid:
            return
        url = f"{STEAMDB_HOST}/app/{appid}/charts/"
        response = self.http.get(url, target="steamdb", timeout=(5, 20))
        if response is None:
            record["steamdb_source_page_url"] = None
            return

        soup = BeautifulSoup(response.text, "html.parser")
        numbers = [n.get_text(strip=True) for n in soup.select("span.number")]
        record["concurrent_players_live"] = _to_int(numbers[0]) if len(numbers) > 0 else None
        record["concurrent_players_24h_peak"] = _to_int(numbers[1]) if len(numbers) > 1 else None
        record["concurrent_players_alltime_peak"] = _to_int(numbers[2]) if len(numbers) > 2 else None

        owners_table = soup.select_one("table.table-owners") or soup.select_one("table#table-owners")
        estimates: dict[str, str] = {}
        if owners_table:
            for tr in owners_table.select("tbody tr"):
                cells = [td.get_text(strip=True) for td in tr.select("td")]
                if len(cells) >= 2:
                    estimates[cells[0]] = cells[1]
        record["owner_estimates"] = estimates or None
        record["steamdb_source_page_url"] = url


# ------------------------------------------------------------------
# 5. 데이터 검증기 (has-a: 중복·결측·자료형 기준을 캡슐화)
# ------------------------------------------------------------------
@dataclass
class ValidationReport:
    original_count: int = 0
    duplicate_removed: int = 0
    missing_key_removed: int = 0
    final_count: int = 0
    notes: list[str] = field(default_factory=list)


class DataValidator:
    """중복·결측·자료형 점검 기준.

    - appid 또는 title 이 없는 레코드는 분석 불가로 판단해 제거한다.
    - appid 기준 중복은 마지막(가장 정보가 많이 채워진) 레코드만 남긴다.
    - 가격/리뷰/동접자 등 숫자 필드는 pandas.to_numeric 으로 강제 변환하고,
      변환 실패 값은 NaN(-> JSON 저장 시 None) 으로 통일해 자료형 혼입을 방지한다.
    """

    NUMERIC_FIELDS = (
        "price_final", "price_original",
        "review_positive_pct", "review_total_count", "recommendations_total",
        "concurrent_players_live", "concurrent_players_24h_peak", "concurrent_players_alltime_peak",
    )

    def validate(self, records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], ValidationReport]:
        report = ValidationReport(original_count=len(records))
        if not records:
            report.notes.append("수집된 레코드가 없습니다.")
            return [], report

        df = pd.DataFrame(records)

        before = len(df)
        df = df.dropna(subset=["appid", "title"])
        report.missing_key_removed = before - len(df)
        report.notes.append(f"필수 필드(appid/title) 결측 {report.missing_key_removed}건을 제거했습니다.")

        before = len(df)
        df = df.drop_duplicates(subset=["appid"], keep="last")
        report.duplicate_removed = before - len(df)
        report.notes.append(f"appid 기준 중복 {report.duplicate_removed}건을 제거했습니다.")

        for col in self.NUMERIC_FIELDS:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        report.final_count = len(df)
        records_out = [_sanitize_for_json(rec) for rec in df.to_dict(orient="records")]
        return records_out, report


# ------------------------------------------------------------------
# 6. 저장소 (has-a: 파일 입출력 책임 분리)
# ------------------------------------------------------------------
class JsonStorage: # 데이터를 기록할 Json 파일 경로에 접근하여 경로를 초기화한 뒤 파일에 데이터를 기록한다.
    def __init__(self, path: Path) -> None:
        self.path = path

    def save(self, records: list[dict[str, Any]], meta: dict[str, Any]) -> None:
        payload = {"meta": meta, "records": records}
        # allow_nan=False: NaN/Infinity 가 남아있다면 표준 JSON에 어긋나므로
        # 조용히 잘못된 파일을 만드는 대신 즉시 명확한 예외로 드러낸다.
        text = json.dumps(payload, ensure_ascii=False, indent=2, default=str, allow_nan=False)
        self.path.write_text(text, encoding="utf-8")
        logger.info("JSON 저장 완료: %s (레코드 %d건)", self.path, len(records))


# ------------------------------------------------------------------
# 7. 파이프라인 (has-a 구성요소 조립)
# ------------------------------------------------------------------
class SteamCrawlPipeline:
    """has-a 방식으로 각 컴포넌트를 조립해 전체 크롤링을 수행하는 오케스트레이터."""

    def __init__(self) -> None:
        self.rate_limiter = RateLimiter()
        self.breaker = CircuitBreaker()
        self.http = HttpClient(self.rate_limiter, self.breaker)
        self.fetcher = PageFetcher(self.http, self.rate_limiter, use_selenium=True)
        self.store_crawler = SteamStoreCrawler(self.fetcher, self.http)
        self.steamdb_crawler = SteamDBCrawler(self.http)
        self.validator = DataValidator()
        self.storage = JsonStorage(DATA_DIR / "steam_games.json")

    def run(self) -> None: # 초기 실행 함수
        try:
            store_allowed = check_robots_allowed(STORE_HOST, "/search/")
            steamdb_allowed = check_robots_allowed(STEAMDB_HOST, "/app/")

            if not store_allowed:
                logger.error("robots.txt 정책상 %s 크롤링이 허용되지 않아 파이프라인을 중단합니다.", STORE_HOST)
                return

            records = self.store_crawler.crawl_search_pages(MIN_PAGES, MIN_RECORDS)

            for idx, rec in enumerate(records, start=1):
                logger.info("[%d/%d] 상세정보 수집: %s (appid=%s)", idx, len(records), rec.get("title"), rec.get("appid"))
                try:
                    self.store_crawler.enrich_with_detail_page(rec)
                    self.store_crawler.fetch_review_aggregate(rec)
                    self.store_crawler.fetch_sample_reviews(rec)
                    if steamdb_allowed:
                        self.steamdb_crawler.enrich_with_steamdb(rec)
                except Exception:  # noqa: BLE001 - 개별 레코드 실패가 전체 파이프라인을 막지 않도록 함
                    logger.exception("레코드 보강 중 예외 발생, 해당 필드는 비워둡니다: appid=%s", rec.get("appid"))

            cleaned, report = self.validator.validate(records)
            logger.info(
                "검증 결과: 원본 %d건 -> 최종 %d건 (결측제거 %d, 중복제거 %d)",
                report.original_count, report.final_count, report.missing_key_removed, report.duplicate_removed,
            )

            meta = { # 메타 데이터 : 필수 데이터 수집을 위해 추가로 수집하는 가상 데이터
                "source_site": STORE_HOST,
                "steamdb_enriched": steamdb_allowed,
                "collected_pages": MIN_PAGES,
                "min_records_required": MIN_RECORDS,
                "validation_report": {
                    "original_count": report.original_count,
                    "duplicate_removed": report.duplicate_removed,
                    "missing_key_removed": report.missing_key_removed,
                    "final_count": report.final_count,
                    "notes": report.notes,
                },
                "generated_at_utc": _utc_now_iso(),
            }
            self.storage.save(cleaned, meta)

            if report.final_count < MIN_RECORDS:
                logger.warning("요구 레코드 수(%d) 미달: 실제 %d건", MIN_RECORDS, report.final_count)
        finally:
            self.fetcher.close()


def main() -> None:
    pipeline = SteamCrawlPipeline()
    pipeline.run()


if __name__ == "__main__":
    main()
