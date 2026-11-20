"""popup_scout.py 테스트: python3 -m unittest test_popup_scout -v"""
import unittest
from datetime import date

from popup_scout import screen, select, render, reject_reason, normalize_key

TODAY = date(2026, 11, 21)


def make(**over):
    c = {
        "name": "윈터 하우스 팝업", "brand": "테스트리빙", "category": "리빙",
        "venue": "더현대 서울 B1", "address": "서울 영등포구 여의대로 108", "region": "서울",
        "start": "2026-11-20", "end": "2026-12-10", "hours": "10:30-20:00",
        "summary": "요약", "why_now": "이유", "action": "행동",
        "source_url": "https://example.com/notice", "source_type": "유통사 공식",
    }
    c.update(over)
    return c


class RuleTests(unittest.TestCase):
    def test_valid(self):
        self.assertIsNone(reject_reason(make(), TODAY))

    def test_open_window(self):
        self.assertIsNone(reject_reason(make(start="2026-11-07"), TODAY))        # 14일 경과: 통과
        self.assertIn("15일 경과", reject_reason(make(start="2026-11-06"), TODAY))
        self.assertIsNone(reject_reason(make(start="2026-12-05", end="2026-12-31"), TODAY))  # D-14
        self.assertIn("15일 남음", reject_reason(make(start="2026-12-06", end="2026-12-31"), TODAY))

    def test_closing_soon(self):
        self.assertIn("3일 이내", reject_reason(make(end="2026-11-24"), TODAY))
        self.assertIsNone(reject_reason(make(end="2026-11-25"), TODAY))
        self.assertIn("이미 종료", reject_reason(make(start="2026-11-10", end="2026-11-20"), TODAY))

    def test_end_unknown_allowed(self):
        self.assertIsNone(reject_reason(make(end=None), TODAY))

    def test_category_and_source(self):
        self.assertIn("카테고리", reject_reason(make(category="패션"), TODAY))
        self.assertIn("공식 출처", reject_reason(make(source_type="언론 보도"), TODAY))
        self.assertIn("필수 항목", reject_reason(make(source_url=""), TODAY))

    def test_dedupe(self):
        history = {normalize_key("테스트리빙더현대 서울 B1")}
        r = screen([make(), make(brand="다른브랜드"), make(brand="다른브랜드")], TODAY, history)
        self.assertEqual([c["brand"] for c in r.accepted], ["다른브랜드"])
        reasons = [reason for _, reason in r.rejected]
        self.assertIn("최근 7일 내 리스트에 이미 소개됨", reasons)
        self.assertIn("오늘 후보 안에서 중복", reasons)


class SelectRenderTests(unittest.TestCase):
    def test_balanced_selection_and_limit(self):
        living = [make(brand=f"리빙{i}") for i in range(8)]
        kids = [make(brand=f"키즈{i}", category="유아동") for i in range(3)]
        picked = select(living + kids, TODAY, 10)
        self.assertEqual(len(picked), 10)
        self.assertEqual(sum(c["category"] == "유아동" for c in picked), 3)

    def test_render_shortfall_warning(self):
        md = render([make()], [], TODAY, 10)
        self.assertIn("1개뿐이라 10개를 채우지 못했습니다", md)
        self.assertIn("테스트리빙 | 더현대 서울 B1", md)


if __name__ == "__main__":
    unittest.main()
