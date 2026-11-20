#!/usr/bin/env python3
"""리빙·유아동 팝업 데일리 — 후보 검증 + 노션 페이지 본문 생성기.

매일 아침 예약 작업(Claude)이 웹에서 팝업 후보를 모아 JSON으로 저장하면,
이 스크립트가 사규(AI_COMPANY.md §3-1 시장조사팀) 기준으로 기계적으로 걸러내고
최종 10개를 노션 페이지 본문(Notion-flavored Markdown)으로 만든다.

판단이 필요한 일(어떤 팝업이 볼만한가, 출처가 공식인가)은 Claude가 하고,
날짜 계산·중복 제거·필수 항목 누락처럼 실수하기 쉬운 일은 이 코드가 한다.

사용법
    python3 popup_scout.py check  --candidates c.json --today 2026-11-21 [--history h.txt]
    python3 popup_scout.py render --candidates c.json --today 2026-11-21 [--history h.txt] [--limit 10]

후보 JSON 형식 (리스트)
    [
      {
        "name": "팝업 이름(공식 표기)",
        "brand": "브랜드명",
        "category": "리빙" | "유아동",
        "venue": "장소명 (예: 더현대 서울 B1 이벤트홀)",
        "address": "주소 또는 '미확인'",
        "region": "서울" 등 시·도,
        "start": "YYYY-MM-DD",
        "end": "YYYY-MM-DD" 또는 null(종료일 미발표),
        "hours": "운영시간 또는 '미확인'",
        "summary": "핵심 요약 1~2문장",
        "why_now": "왜 지금 가야 하는가 1문장",
        "action": "오늘 써먹을 행동 1개 (예약 링크, 오픈런 시간 등)",
        "source_url": "공식 출처 URL (브랜드·백화점·운영사 공지/보도자료)",
        "source_type": "브랜드 공식" | "유통사 공식" | "보도자료" | "언론 보도"
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
from datetime import date, timedelta
from pathlib import Path

# 사규 §3-1 시장조사팀 기준
OPEN_WINDOW_DAYS = 14   # 오픈 후 14일 초과면 반려, 14일 넘게 남은 예정 건도 반려
CLOSING_SOON_DAYS = 3   # 종료일이 오늘로부터 3일 이내면 반려
DEFAULT_LIMIT = 10

CATEGORIES = ("리빙", "유아동")
OFFICIAL_SOURCE_TYPES = ("브랜드 공식", "유통사 공식", "보도자료")
REQUIRED_FIELDS = (
    "name", "brand", "category", "venue", "address", "region",
    "start", "summary", "why_now", "action", "source_url", "source_type",
)
UNKNOWN = "미확인"


@dataclass
class Result:
    accepted: list[dict] = field(default_factory=list)
    rejected: list[tuple[dict, str]] = field(default_factory=list)


def normalize_key(text: str) -> str:
    """중복 비교용 키: 공백·기호·대소문자 차이를 없앤다."""
    text = unicodedata.normalize("NFKC", text or "").lower()
    text = re.sub(r"(팝업\s*스토어|팝업|popup\s*store|pop-?up)", "", text)
    return re.sub(r"[^0-9a-z가-힣]", "", text)


def candidate_key(c: dict) -> str:
    return normalize_key(f"{c.get('brand', '')}{c.get('venue', '')}")


def parse_date(value) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value).strip())
    except ValueError:
        return None


def load_history(path: str | None) -> set[str]:
    """최근 7일 노션 페이지에서 뽑은 '브랜드 | 장소' 목록 (한 줄에 하나)."""
    if not path:
        return set()
    keys = set()
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        brand, _, venue = line.partition("|")
        keys.add(normalize_key(f"{brand}{venue}"))
    return keys


def reject_reason(c: dict, today: date) -> str | None:
    missing = [f for f in REQUIRED_FIELDS if not str(c.get(f) or "").strip()]
    if missing:
        return f"필수 항목 누락: {', '.join(missing)}"
    if c["category"] not in CATEGORIES:
        return f"카테고리 '{c['category']}'는 리빙·유아동이 아님"
    if c["source_type"] not in OFFICIAL_SOURCE_TYPES:
        return f"공식 출처 아님 ({c['source_type']})"
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

    if (today - start).days > OPEN_WINDOW_DAYS:
        return f"오픈 {(today - start).days}일 경과 ({OPEN_WINDOW_DAYS}일 초과)"
    if (start - today).days > OPEN_WINDOW_DAYS:
        return f"오픈까지 {(start - today).days}일 남음 ({OPEN_WINDOW_DAYS}일 초과)"
    if end and (end - today).days <= CLOSING_SOON_DAYS:
        if end < today:
            return "이미 종료됨"
        return f"종료 {(end - today).days}일 전 ({CLOSING_SOON_DAYS}일 이내)"
    return None


def screen(candidates: list[dict], today: date, history: set[str]) -> Result:
    result = Result()
    seen: set[str] = set()
    for c in candidates:
        reason = reject_reason(c, today)
        key = candidate_key(c)
        if reason is None and key in history:
            reason = "최근 7일 내 리스트에 이미 소개됨"
        if reason is None and key in seen:
            reason = "오늘 후보 안에서 중복"
        if reason:
            result.rejected.append((c, reason))
        else:
            seen.add(key)
            result.accepted.append(c)
    return result


def status_label(c: dict, today: date) -> str:
    start = parse_date(c["start"])
    diff = (start - today).days
    if diff > 0:
        return f"D-{diff} 오픈 예정"
    if diff == 0:
        return "오늘 오픈"
    return f"운영 중 (오픈 {-diff}일차)"


def period_text(c: dict) -> str:
    start = parse_date(c["start"])
    end = parse_date(c.get("end"))
    s = f"{start.year}.{start.month}.{start.day}"
    if end is None:
        return f"{s} – 종료일 {UNKNOWN}"
    if end.year == start.year:
        return f"{s} – {end.month}.{end.day}"
    return f"{s} – {end.year}.{end.month}.{end.day}"


def select(accepted: list[dict], today: date, limit: int) -> list[dict]:
    """곧 오픈/막 오픈한 순으로 정렬하되 리빙·유아동이 한쪽에 몰리지 않게 번갈아 고른다."""
    def recency(c):
        return abs((parse_date(c["start"]) - today).days)

    buckets = {cat: sorted([c for c in accepted if c["category"] == cat], key=recency)
               for cat in CATEGORIES}
    picked: list[dict] = []
    while len(picked) < limit and any(buckets.values()):
        for cat in CATEGORIES:
            if buckets[cat] and len(picked) < limit:
                picked.append(buckets[cat].pop(0))
    return picked


def esc(text) -> str:
    """노션 표 셀용 이스케이프."""
    return str(text if text not in (None, "") else UNKNOWN).replace("|", "\\|").replace("\n", " ")


def render(picked: list[dict], rejected: list[tuple[dict, str]], today: date, limit: int,
           spare: list[dict] | None = None) -> str:
    counts = {cat: sum(1 for c in picked if c["category"] == cat) for cat in CATEGORIES}
    lines = [
        f"<callout icon=\"📌\" color=\"gray_bg\">",
        f"기준일 {today.isoformat()} · 리빙 {counts['리빙']}개 · 유아동 {counts['유아동']}개 · "
        f"오픈 ±{OPEN_WINDOW_DAYS}일 이내, 종료 {CLOSING_SOON_DAYS}일 이내 제외, 최근 7일 중복 제외, 공식 출처만",
        "</callout>",
        "",
    ]
    if len(picked) < limit:
        lines += [
            f"<callout icon=\"⚠️\" color=\"yellow_bg\">",
            f"기준을 통과한 팝업이 {len(picked)}개뿐이라 {limit}개를 채우지 못했습니다. 기준 미달 건으로 채우지 않았습니다.",
            "</callout>",
            "",
        ]

    lines += [
        "## 한눈에 보기",
        "",
        "| # | 팝업 | 분류 | 지역 | 기간 | 상태 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for i, c in enumerate(picked, 1):
        lines.append(
            f"| {i} | {esc(c['brand'])} — {esc(c['name'])} | {esc(c['category'])} | "
            f"{esc(c['region'])} | {esc(period_text(c))} | {esc(status_label(c, today))} |"
        )
    lines.append("")

    lines += ["## 상세", ""]
    for i, c in enumerate(picked, 1):
        lines += [
            f"### {i}. {c['brand']} — {c['name']}",
            "",
            f"- **분류**: {c['category']} · {status_label(c, today)}",
            f"- **기간**: {period_text(c)}",
            f"- **장소**: {c['venue']} ({c.get('address') or UNKNOWN})",
            f"- **운영시간**: {c.get('hours') or UNKNOWN}",
            f"- **핵심 요약**: {c['summary']}",
            f"- **왜 지금**: {c['why_now']}",
            f"- **오늘 할 행동**: {c['action']}",
            f"- **출처** ({c['source_type']}): [{c['source_url']}]({c['source_url']})",
            "",
        ]

    if spare:
        lines += ["<toggle heading=\"h3\">", f"예비 후보 {len(spare)}개 (기준 통과, 10위 밖)", ""]
        for c in spare:
            lines.append(f"- [{c['category']}] {c['brand']} — {c['name']} · {period_text(c)} · [출처]({c['source_url']})")
        lines += ["</toggle>", ""]

    if rejected:
        lines += ["<toggle heading=\"h3\">", "제외된 후보와 이유", ""]
        for c, reason in rejected:
            lines.append(f"- {c.get('brand') or '?'} — {c.get('name') or '?'}: {reason}")
        lines += ["</toggle>", ""]

    # 다음 날 중복 체크용 키 (사람은 안 봐도 되는 부분)
    lines += ["<toggle heading=\"h3\">", "중복 체크용 키 (자동 사용)", ""]
    lines += [f"- {c['brand']} | {c['venue']}" for c in picked]
    lines += ["</toggle>"]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", choices=("check", "render"))
    p.add_argument("--candidates", required=True)
    p.add_argument("--today", default=date.today().isoformat(), help="기준일 YYYY-MM-DD (한국 날짜)")
    p.add_argument("--history", help="최근 7일 '브랜드 | 장소' 목록 파일")
    p.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    args = p.parse_args(argv)

    today = date.fromisoformat(args.today)
    candidates = json.loads(Path(args.candidates).read_text(encoding="utf-8"))
    result = screen(candidates, today, load_history(args.history))

    if args.command == "check":
        print(f"통과 {len(result.accepted)}개 / 제외 {len(result.rejected)}개 (목표 {args.limit}개)")
        for c in result.accepted:
            print(f"  O [{c['category']}] {c['brand']} — {c['name']} ({status_label(c, today)})")
        for c, reason in result.rejected:
            print(f"  X {c.get('brand') or '?'} — {c.get('name') or '?'}: {reason}")
        return 0

    picked = select(result.accepted, today, args.limit)
    spare = [c for c in result.accepted if c not in picked]
    print(render(picked, result.rejected, today, args.limit, spare))
    return 0


if __name__ == "__main__":
    sys.exit(main())
