"""popup_scout.py 테스트: cd popup_scout && python3 -m unittest test_popup_scout -v"""
import tempfile
import unittest
from datetime import date

from popup_scout import index_rows, load_history, reject_reason, render, screen, select, status

TODAY = date(2026, 11, 21)


def make(**over):
    c = {
        "name": "윈터 하우스", "brand": "테스트리빙", "category": "리빙", "subcategory": "가구·인테리어",
        "venue": "더현대 서울 B1", "address": "서울 영등포구 여의대로 108", "region": "서울",
        "start": "2026-11-20", "end": "2026-12-10",
        "summary": "요약", "highlights": "연출", "reference": "참고", "channel": "백화점",
        "space_size": "약 100평", "photo_url": "https://example.com/photo",
        "source_url": "https://example.com/news", "source_type": "보도 기반 기사",
    }
    c.update(over)
    return c


class RuleTests(unittest.TestCase):
    def test_valid(self):
        self.assertIsNone(reject_reason(make(), TODAY))

    def test_six_month_window(self):
        # 종료일 기준 183일 이내면 통과, 넘으면 제외
        self.assertIsNone(reject_reason(make(start="2026-05-01", end="2026-05-22"), TODAY))
        self.assertIn("6개월 초과", reject_reason(make(start="2026-05-01", end="2026-05-21"), TODAY))
        # 종료일 미확인이면 오픈일 기준
        self.assertIn("6개월 초과", reject_reason(make(start="2026-05-01", end=None), TODAY))

    def test_upcoming_window(self):
        self.assertIsNone(reject_reason(make(start="2026-12-05", end="2026-12-31"), TODAY))
        self.assertIn("15일 남음", reject_reason(make(start="2026-12-06", end="2026-12-31"), TODAY))

    def test_sources(self):
        for ok in ("브랜드 공식", "유통사 공식", "보도자료", "보도 기반 기사"):
            self.assertIsNone(reject_reason(make(source_type=ok), TODAY))
        self.assertIn("출처 부족", reject_reason(make(source_type="블로그·SNS"), TODAY))
        self.assertIn("출처 부족", reject_reason(make(source_type="정보 사이트"), TODAY))

    def test_categories(self):
        self.assertIn("리빙·유아동이 아님", reject_reason(make(category="패션"), TODAY))
        self.assertEqual(reject_reason(make(start=""), TODAY), "오픈일 미확인")
        self.assertIn("목록에 없음", reject_reason(make(subcategory="캐릭터"), TODAY))  # 유아동 세부를 리빙에
        self.assertIsNone(reject_reason(make(category="유아동", subcategory="캐릭터"), TODAY))

    def test_status(self):
        self.assertEqual(status(make(), TODAY)[1], "운영 중")
        self.assertEqual(status(make(start="2026-11-25"), TODAY)[1], "오픈 예정 D-4")
        self.assertEqual(status(make(start="2026-10-01", end="2026-10-11"), TODAY)[1], "종료 (41일 전)")


class HistoryTests(unittest.TestCase):
    def test_history_from_notion_table(self):
        page = (
            "## 누적 목록\n\n"
            "| 소개일 | 분류 | 브랜드 | 팝업 | 장소 | 시작일 |\n"
            "| --- | --- | --- | --- | --- | --- |\n"
            + index_rows([make()], date(2026, 11, 20)) + "\n\n다음 문단\n"
        )
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as f:
            f.write(page)
        history = load_history(f.name)
        r = screen([make(), make(venue="신세계 강남점"), make(brand="다른", ), make(brand="다른")], TODAY, history)
        self.assertEqual([(c["brand"], c["venue"]) for c in r.accepted],
                         [("테스트리빙", "신세계 강남점"), ("다른", "더현대 서울 B1")])
        reasons = [reason for _, reason in r.rejected]
        self.assertEqual(reasons, ["이전 리스트에서 이미 소개함", "오늘 후보 안에서 중복"])


class SelectRenderTests(unittest.TestCase):
    def test_balance_and_subcategory_cap(self):
        living = [make(brand=f"가구{i}") for i in range(6)] + [make(brand="향", subcategory="홈프래그런스")]
        kids = [make(brand=f"키즈{i}", category="유아동", subcategory="완구·놀이") for i in range(2)]
        picked = select(living + kids, TODAY, 10)
        self.assertEqual(len(picked), 9)  # 후보 전체가 9개
        self.assertEqual(sum(c["category"] == "유아동" for c in picked), 2)
        # 상한 때문에 '향'이 가구 4번째보다 먼저 뽑혀야 함
        first_six = [c["brand"] for c in picked[:6]]
        self.assertIn("향", first_six)

    def test_ongoing_before_ended(self):
        ended = make(brand="끝남", start="2026-09-01", end="2026-09-10")
        ongoing = make(brand="진행", start="2026-11-01", end="2026-12-31")
        self.assertEqual(select([ended, ongoing], TODAY, 1)[0]["brand"], "진행")

    def test_priority_channel_first(self):
        mall = make(brand="몰", channel="복합몰", start="2026-11-19")
        dept = make(brand="백화점", channel="백화점", start="2026-11-01")
        self.assertEqual(select([mall, dept], TODAY, 1)[0]["brand"], "백화점")

    def test_photo_and_channel_checks(self):
        self.assertIn("사진 URL", reject_reason(make(photo_url="photo.jpg"), TODAY))
        self.assertIn("채널", reject_reason(make(channel="온라인"), TODAY))
        self.assertIsNone(reject_reason(make(photo_url=None, space_size=None, channel=None), TODAY))

    def test_render_shortfall(self):
        md = render([make()], [], TODAY, 10)
        self.assertIn("1개뿐이라 10개를 채우지 못했습니다", md)
        self.assertIn("참고 포인트", md)
        self.assertIn("[보기](https://example.com/photo)", md)
        self.assertIn("**공간 규모**: 약 100평", md)


if __name__ == "__main__":
    unittest.main()
