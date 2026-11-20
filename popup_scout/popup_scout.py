#!/usr/bin/env python3
"""리빙·유아동 팝업 데일리 — 후보 검증 + 노션 페이지 본문 생성기.

목적: 팝업 기획·연출을 참고하기 위한 레퍼런스 리스트. 지금 운영 중이 아니어도 된다.

매일 아침 예약 작업(Claude)이 웹에서 팝업 후보를 모아 JSON으로 저장하면,
이 스크립트가 기준에 맞춰 기계적으로 걸러내고 10개를 노션 페이지 본문
(Notion-flavored Markdown)으로 만든다.

판단이 필요한 일(어떤 팝업이 참고할 만한가, 출처가 믿을 만한가)은 Claude가 하고,
날짜 계산·중복 제거·필수 항목 누락처럼 실수하기 쉬운 일은 이 코드가 한다.

기준
- 기간: 최근 6개월(183일) 안에 운영한 팝업 + 14일 안에 오픈 예정인 팝업
        (종료일이 있으면 종료일, 없으면 오픈일이 183일 이내여야 함)
- 출처: 브랜드 공식 / 유통사 공식 / 보도자료 / 보도 기반 기사(브랜드·유통사 발표를 인용한 기사)
        정보 사이트·블로그·SNS 후기만 있는 건은 제외
- 중복: 지금까지 소개한 팝업(상위 노션 페이지의 누적 목록)과 같으면 제외
- 분류: 리빙·유아동 두 갈래, 세부 분류는 SUBCATEGORIES 참고

사용법
    python3 popup_scout.py check  --candidates c.json --today 2026-11-21 [--history index.md]
    python3 popup_scout.py render --candidates c.json --today 2026-11-21 [--history index.md]
    python3 popup_scout.py index-rows --candidates c.json --today 2026-11-21 [--history index.md]

    --history 에는 상위 노션 페이지 본문(또는 그중 '누적 목록' 표)을 저장한 파일을 넘긴다.
    표 머리글에 '브랜드', '장소', '시작일' 칸이 있으면 자동으로 읽는다.

후보 JSON 형식 (리스트)
    [
      {
        "name": "팝업 이름(공식 표기)",
        "brand": "브랜드명",
        "category": "리빙" | "유아동",
        "subcategory": "가구·인테리어" 등 (SUBCATEGORIES 중 하나),
        "venue": "장소명 (예: 더현대 서울 B1 이벤트홀)",
        "address": "주소 또는 '미확인'",
        "region": "서울" 등 시·도,
        "start": "YYYY-MM-DD",
        "end": "YYYY-MM-DD" 또는 null(종료일 미발표),
        "summary": "어떤 팝업인지 1~2문장",
        "highlights": "공간 연출·체험 프로그램·한정 굿즈 등 눈에 띄는 요소",
        "reference": "기획에 참고할 점 1문장",
        "source_url": "출처 URL",
        "source_type": "브랜드 공식" | "유통사 공식" | "보도자료" | "보도 기반 기사" | "정보 사이트" | "블로그·SNS"
      }
    ]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

LOOKBACK_DAYS = 183      # 최근 6개월
UPCOMING_DAYS = 14       # 오픈 예정은 14일 이내만
MAX_PER_SUBCATEGORY = 3  # 한 세부 분류가 리스트를 독차지하지 않게
DEFAULT_LIMIT = 10

CATEGORIES = ("리빙", "유아동")
SUBCATEGORIES = {
    "리빙": (
        "가구·인테리어", "홈데코·조명", "주방·테이블웨어", "침구·패브릭",
        "홈프래그런스", "생활가전", "식물·가드닝", "문구·디자인 소품",
        "라이프스타일 편집샵", "반려동물 리빙", "리빙 기타",
    ),
    "유아동": (
        "키즈 패션", "완구·놀이", "캐릭터", "그림책·교구", "육아용품",
        "유아식·키즈 푸드", "가족 체험형", "유아동 기타",
    ),
}
ACCEPTED_SOURCE_TYPES = ("브랜드 공식", "유통사 공식", "보도자료", "보도 기반 기사")
REQUIRED_FIELDS = (
    "name", "brand", "category", "subcategory", "venue", "region", "start",
    "summary", "highlights", "reference", "source_url", "source_type",
)
UNKNOWN = "미확인"


@dataclass
class Result:
    accepted: list[dict] = field(default_factory=list)
    rejected: list[tuple[dict, str]] = field(default_factory=list)


# ---------- 중복 판정 ----------

def normalize(text: str) -> str:
    """비교용 정규화: 공백·기호·대소문자 차이를 없앤다."""
    text = unicodedata.normalize("NFKC", text or "").lower()
    text = re.sub(r"(팝업\s*스토어|팝업|popup\s*store|pop-?up)", "", text)
    return re.sub(r"[^0-9a-z가-힣]", "", text)


def make_key(brand: str, venue: str, start: str) -> str:
    """같은 브랜드라도 장소나 시기(월)가 다르면 다른 팝업으로 본다."""
    return f"{normalize(brand)}|{normalize(venue)}|{str(start or '')[:7]}"


def candidate_key(c: dict) -> str:
    return make_key(c.get("brand", ""), c.get("venue", ""), c.get("start", ""))


def split_row(line: str) -> list[str]:
    cells = re.split(r"(?<!\\)\|", line.strip().strip("|"))
    return [re.sub(r"<[^>]+>", "", cell).replace("\\|", "|").strip() for cell in cells]


def load_history(path: str | None) -> set[str]:
    """노션 '누적 목록' 표에서 (브랜드, 장소, 시작일)을 읽어 중복 키를 만든다."""
    if not path:
        return set()
    keys: set[str] = set()
    cols: dict[str, int] | None = None
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip().startswith("|"):
            cols = None  # 표가 끝나면 머리글 초기화
            continue
        cells = split_row(line)
        if cols is None:
            if {"브랜드", "장소", "시작일"} <= set(cells):
                cols = {name: cells.index(name) for name in ("브랜드", "장소", "시작일")}
            continue
        if all(set(cell) <= set("-: ") for cell in cells):
            continue  # 구분선
        try:
            keys.add(make_key(cells[cols["브랜드"]], cells[cols["장소"]], cells[cols["시작일"]]))
        except IndexError:
            continue
    return keys


# ---------- 기준 검사 ----------

def parse_date(value) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value).strip())
    except ValueError:
        return None


def reject_reason(c: dict, today: date) -> str | None:
    missing = [f for f in REQUIRED_FIELDS if not str(c.get(f) or "").strip()]
    if "start" in missing:
        return "오픈일 미확인"
    if missing:
        return f"필수 항목 누락: {', '.join(missing)}"
    if c["category"] not in CATEGORIES:
        return f"리빙·유아동이 아님 ({c['category']})"
    if c["subcategory"] not in SUBCATEGORIES[c["category"]]:
        return f"세부 분류 '{c['subcategory']}'가 {c['category']} 목록에 없음"
    if c["source_type"] not in ACCEPTED_SOURCE_TYPES:
        return f"출처 부족 ({c['source_type']}만 있음)"
    if not str(c["source_url"]).startswith(("http://", "https://")):
        return "출처 URL 형식 오류"

    start = parse_date(c["start"])
    if start is None:
        return f"오픈일 형식 오류 ({c['start']})"
    end = parse_date(c.get("end"))
    if c.get("end") and end is None:
        return f"종료일 형식 오류 ({c['end']})"
    if end and end < start:
        return "종료일이 오픈일보다 빠름"

    if (start - today).days > UPCOMING_DAYS:
        return f"오픈까지 {(start - today).days}일 남음 ({UPCOMING_DAYS}일 초과)"
    last_day = end or start
    if (today - last_day).days > LOOKBACK_DAYS:
        return f"{'종료' if end else '오픈'} 후 {(today - last_day).days}일 경과 (6개월 초과)"
    return None


def screen(candidates: list[dict], today: date, history: set[str]) -> Result:
    result = Result()
    seen: set[str] = set()
    for c in candidates:
        reason = reject_reason(c, today)
        key = candidate_key(c)
        if reason is None and key in history:
            reason = "이전 리스트에서 이미 소개함"
        if reason is None and key in seen:
            reason = "오늘 후보 안에서 중복"
        if reason:
            result.rejected.append((c, reason))
        else:
            seen.add(key)
            result.accepted.append(c)
    return result


# ---------- 선정 ----------

def status(c: dict, today: date) -> tuple[int, str]:
    """(정렬 순위, 표시 문구). 운영 중 → 오픈 예정 → 종료 순으로 앞에 둔다."""
    start = parse_date(c["start"])
    end = parse_date(c.get("end"))
    if start > today:
        return 1, f"오픈 예정 D-{(start - today).days}"
    if end is None:
        return 0, "운영 중 추정 (종료일 미확인)"
    if end >= today:
        return 0, "운영 중"
    return 2, f"종료 ({(today - end).days}일 전)"


def select(accepted: list[dict], today: date, limit: int) -> list[dict]:
    """리빙·유아동을 번갈아 고르고, 한 세부 분류는 최대 3개까지. 자리가 남으면 상한 없이 채운다."""
    def order(c):
        return (status(c, today)[0], -parse_date(c["start"]).toordinal())

    queues = {cat: sorted((c for c in accepted if c["category"] == cat), key=order) for cat in CATEGORIES}
    picked: list[dict] = []
    per_sub: dict[str, int] = {}

    def take(cat: str, capped: bool) -> bool:
        for c in queues[cat]:
            if capped and per_sub.get(c["subcategory"], 0) >= MAX_PER_SUBCATEGORY:
                continue
            queues[cat].remove(c)
            picked.append(c)
            per_sub[c["subcategory"]] = per_sub.get(c["subcategory"], 0) + 1
            return True
        return False

    for capped in (True, False):
        progress = True
        while len(picked) < limit and progress:
            progress = False
            for cat in CATEGORIES:
                if len(picked) < limit and take(cat, capped):
                    progress = True
    return picked


# ---------- 출력 ----------

def period_text(c: dict) -> str:
    start = parse_date(c["start"])
    end = parse_date(c.get("end"))
    s = f"{start.year}.{start.month}.{start.day}"
    if end is None:
        return f"{s} – 종료일 {UNKNOWN}"
    if end.year == start.year:
        return f"{s} – {end.month}.{end.day}"
    return f"{s} – {end.year}.{end.month}.{end.day}"


def cell(text) -> str:
    """노션 표 셀용 이스케이프."""
    return str(text if text not in (None, "") else UNKNOWN).replace("|", "\\|").replace("\n", " ")


def render(picked: list[dict], rejected: list[tuple[dict, str]], today: date, limit: int,
           spare: list[dict] | None = None) -> str:
    counts = {cat: sum(1 for c in picked if c["category"] == cat) for cat in CATEGORIES}
    lines = [
        "<callout icon=\"📌\" color=\"gray_bg\">",
        f"기준일 {today.isoformat()} · 리빙 {counts['리빙']}개 · 유아동 {counts['유아동']}개 · "
        "최근 6개월 안에 운영했거나 14일 안에 오픈 예정인 팝업 · 이전 리스트와 중복 제외",
        "</callout>",
        "",
    ]
    if len(picked) < limit:
        lines += [
            "<callout icon=\"⚠️\" color=\"yellow_bg\">",
            f"기준을 통과한 팝업이 {len(picked)}개뿐이라 {limit}개를 채우지 못했습니다. 기준 미달 건으로 채우지 않았습니다.",
            "</callout>",
            "",
        ]

    lines += [
        "## 한눈에 보기",
        "",
        "| # | 브랜드 | 팝업 | 분류 | 지역 | 기간 | 상태 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for i, c in enumerate(picked, 1):
        lines.append(
            f"| {i} | {cell(c['brand'])} | {cell(c['name'])} | {cell(c['category'])} · {cell(c['subcategory'])} | "
            f"{cell(c['region'])} | {cell(period_text(c))} | {cell(status(c, today)[1])} |"
        )
    lines.append("")

    lines += ["## 상세", ""]
    for i, c in enumerate(picked, 1):
        lines += [
            f"### {i}. {c['brand']} — {c['name']}",
            "",
            f"- **분류**: {c['category']} · {c['subcategory']} · {status(c, today)[1]}",
            f"- **기간**: {period_text(c)}",
            f"- **장소**: {c['venue']} ({c.get('address') or UNKNOWN})",
            f"- **요약**: {c['summary']}",
            f"- **눈에 띄는 요소**: {c['highlights']}",
            f"- **참고 포인트**: {c['reference']}",
            f"- **출처** ({c['source_type']}): [{c['source_url']}]({c['source_url']})",
            "",
        ]

    if spare:
        lines += ["<toggle heading=\"h3\">", f"예비 후보 {len(spare)}개 (기준 통과, 10위 밖)", ""]
        for c in spare:
            lines.append(f"- [{c['category']} · {c['subcategory']}] {c['brand']} — {c['name']} · "
                         f"{period_text(c)} · [출처]({c['source_url']})")
        lines += ["</toggle>", ""]

    if rejected:
        lines += ["<toggle heading=\"h3\">", "제외된 후보와 이유", ""]
        for c, reason in rejected:
            lines.append(f"- {c.get('brand') or '?'} — {c.get('name') or '?'}: {reason}")
        lines += ["</toggle>"]
    return "\n".join(lines).rstrip()


def index_rows(picked: list[dict], today: date) -> str:
    """상위 페이지 '누적 목록' 표에 붙일 행. 칸: 소개일 | 분류 | 브랜드 | 팝업 | 장소 | 시작일"""
    return "\n".join(
        f"| {today.isoformat()} | {cell(c['category'])} · {cell(c['subcategory'])} | {cell(c['brand'])} | "
        f"{cell(c['name'])} | {cell(c['venue'])} | {cell(c['start'])} |"
        for c in picked
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", choices=("check", "render", "index-rows"))
    p.add_argument("--candidates", required=True)
    p.add_argument("--today", default=date.today().isoformat(), help="기준일 YYYY-MM-DD (한국 날짜)")
    p.add_argument("--history", help="상위 노션 페이지 본문(누적 목록 표 포함)을 저장한 파일")
    p.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    args = p.parse_args(argv)

    today = date.fromisoformat(args.today)
    candidates = json.loads(Path(args.candidates).read_text(encoding="utf-8"))
    result = screen(candidates, today, load_history(args.history))
    picked = select(result.accepted, today, args.limit)

    if args.command == "check":
        print(f"통과 {len(result.accepted)}개 / 제외 {len(result.rejected)}개 / 선정 {len(picked)}개 (목표 {args.limit}개)")
        for c in picked:
            print(f"  O [{c['category']}·{c['subcategory']}] {c['brand']} — {c['name']} ({status(c, today)[1]})")
        for c in result.accepted:
            if c not in picked:
                print(f"  - 예비 [{c['category']}·{c['subcategory']}] {c['brand']} — {c['name']}")
        for c, reason in result.rejected:
            print(f"  X {c.get('brand') or '?'} — {c.get('name') or '?'}: {reason}")
    elif args.command == "render":
        spare = [c for c in result.accepted if c not in picked]
        print(render(picked, result.rejected, today, args.limit, spare))
    else:
        print(index_rows(picked, today))
    return 0


if __name__ == "__main__":
    sys.exit(main())
