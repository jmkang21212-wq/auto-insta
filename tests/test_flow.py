"""2026-10-28 밤 결정 시험: 릴스 캡션 짧게 + 캐러셀 자세히(둘 다 게시), 시간대(미국 아침·한국 오후, 겹치면 2시간 뒤),
공모주 여러 번 다루기(상장 전날·확약 해제), 음악 없을 때 그대로 게시. pytest로도 `python tests/test_flow.py` 로도 돈다."""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gongsi import cards  # noqa: E402
from gongsi.hook import Hook  # noqa: E402
from gongsi.models import IpoFacts  # noqa: E402
from gongsi.verify import check_caption_layout, check_forbidden, check_numbers  # noqa: E402

# 티앤이코리아 실제 값 (증권신고서 20261016000158 정정 후, 발행실적보고서 20261023000136)
TNE = dict(corp_name="티앤이코리아", market="코스닥", band_low_krw=12600, band_high_krw=15300, final_price_krw=15300,
           demand_ratio=1041.02, listing_date="2026-10-29", underwriters=["신한투자증권"],
           retail_alloc=600000, retail_qty=752036560, retail_applicants=208413, retail_amount_krw=5753079684000, result_rcept_no="20261023000136",
           valuation_krw=21304, valuation_discount_low_pct=28.18, valuation_discount_high_pct=40.86, valuation_model="PER",
           valuation_multiple=19.48, valuation_basis="추정치 (2027년 추정 당기순이익)", valuation_basis_year=2027,
           float_schedule=[{"months": 0, "shares": 3551856, "pct": 31.29}, {"months": 1, "shares": 5337354, "pct": 47.02},
                           {"months": 2, "shares": 6809889, "pct": 59.99}, {"months": 3, "shares": 7820156, "pct": 68.89},
                           {"months": 6, "shares": 7820156, "pct": 68.89}, {"months": 12, "shares": 7820156, "pct": 68.89},
                           {"months": 30, "shares": 11351096, "pct": 100.0}])
HOOK = Hook(title="'초강력 드라이기' 만드는 회사가 상장함", term="터보블로워", emoji="💨", sub="머리 말리는 거 아님",
            video="hair dryer", explain=": '초강력 드라이기'는 터보블로워를 빗댄 말임")


def test_reel_caption_is_short_and_points_to_cards():
    """릴스 캡션 = 정보 + 간단한 설명. 비교(참고로 …)·자금 용도·위험은 캐러셀 캡션에만"""
    f = IpoFacts(**TNE)
    reel = cards.reel_caption(f, "20261016000158", None, "증권신고서", HOOK, credit="📷 자료 화면: Pixabay")
    full = cards.caption(f, "20261016000158", None, "증권신고서", HOOK, credit="📷 자료 화면: Pixabay")
    assert len(reel) < len(full) * 0.6, (len(reel), len(full))
    assert "참고로" not in reel and "참고로" in full  # 비교는 캐러셀 캡션에만
    flat = reel.replace("\n", " ")
    assert "카드뉴스" in reel and "적정주가는 아니라고" in flat
    assert "화면 속 15,300원 → 21,304원은" in flat and "확정 공모가 → 주관사가 매긴 주당 몸값" in flat  # 화면 숫자 풀이
    assert not check_caption_layout(reel) and not check_forbidden([reel])
    from gongsi.models import Summary
    _, derived = cards.build(f, Summary(business_lines=[], evidence=[]), date(2026, 10, 28), "x")  # 계산값(일반 청약 경쟁률 등)
    bad = [p for p in check_numbers([re.sub(r"\d{14}", "", reel)], f) if not any(f"'{k}'" in p for k in derived)]
    assert not bad, bad


def test_slots_us_morning_kr_afternoon_two_hours_apart():
    from gongsi.publish import slots
    morning = datetime(2026, 10, 29, 7, 30, tzinfo=slots.KST)
    a = slots.assign("KR", date(2026, 10, 29), set(), morning)
    b = slots.assign("KR", date(2026, 10, 29), {a}, morning)
    assert a.endswith("T13:00:00+09:00") and b.endswith("T15:00:00+09:00"), (a, b)  # 겹치면 둘 다, 2시간 간격
    assert slots.assign("US", date(2026, 10, 29), set(), morning).endswith("T08:00:00+09:00")
    late = datetime(2026, 10, 29, 19, 30, tzinfo=slots.KST)
    assert slots.assign("KR", date(2026, 10, 29), set(), late) is None  # 그날 자리가 다 지났으면 승인 즉시
    assert not slots.due(a, morning) and slots.due(a, datetime(2026, 10, 29, 13, 0, 1, tzinfo=slots.KST)) and slots.due(None)


def test_phase_titles():
    from gongsi.hook import phase_title
    t = HOOK.title
    assert phase_title(t, "listing", "2026-10-29", date(2026, 10, 28)) == "'초강력 드라이기' 만드는 회사가 내일 상장함"
    assert phase_title(t, "listing", "2026-11-02", date(2026, 10, 30)) == "'초강력 드라이기' 만드는 회사가 월요일 상장함"
    assert phase_title(phase_title(t, "listing", "2026-10-29", date(2026, 10, 28)), "lockup1") == "'초강력 드라이기' 만드는 회사, 묶인 주식 풀림"
    assert phase_title("꼬리가 다른 제목", "lockup1") == "꼬리가 다른 제목"


def test_lockup_release_dates_and_caption():
    """확약 해제편: 누적표에서 실제로 늘어나는 달만, 풀리기 전 마지막 평일에. 숫자는 신고서 그대로 + 차이는 계산값"""
    from gongsi.followups import due_today, lockup_caption, releases
    f = IpoFacts(**TNE)
    rel = releases(f)
    assert [r["months"] for r in rel] == [1, 2, 3, 30]  # 6·12개월은 안 늘어남
    one = rel[0]
    assert one["date"] == date(2026, 11, 29) and one["added"] == 1785498
    assert due_today(one, date(2026, 11, 27)) and not due_today(one, date(2026, 11, 26))  # 29일(일) → 27일(금)에 게시
    cap = lockup_caption(f, "20261016000158", "증권신고서", Hook(**{**HOOK.__dict__, "title": "'초강력 드라이기' 만드는 회사, 묶인 주식 풀림"}), one)
    flat = cap.replace("\n", " ")
    assert "31.29% → 47.02%" in flat and "1,785,498주" in flat and "확약 물량은 빠진 숫자" in flat
    assert not check_caption_layout(cap) and not check_forbidden([cap])
    bad = [p for p in check_numbers([re.sub(r"\d{14}", "", cap)], f) if "'1,785,498'" not in p]
    assert not bad, bad


def test_publish_both_cards_first_then_reel_and_retry_skips_cards():
    """둘 다 게시: 캐러셀(자세한 캡션) 먼저, 릴스(짧은 캡션) 다음. 릴스가 실패해 다시 하면 캐러셀은 또 안 올림"""
    from gongsi.publish import __main__ as pub, instagram, store
    calls = []
    os.environ["GONGSI_REPO"] = "o/r"
    with tempfile.TemporaryDirectory(dir=str(store.ROOT)) as tmp:
        d = Path(tmp)
        for i in (1, 2):
            (d / f"0{i}.jpg").write_bytes(b"x")
        (d / "reel.mp4").write_bytes(b"x")
        (d / "caption.txt").write_text("자세한 판", encoding="utf-8")
        (d / "caption_reel.txt").write_text("짧은 판", encoding="utf-8")
        post = store.Post(d, {"corp": "시험", "rcept_no": "1", "date": "2026-10-29", "n": 2, "status": "approved",
                              "format": "both", "reel": "reel.mp4"})
        post.save()
        fail = {"reel": True}

        def fake_carousel(urls, cap):
            calls.append(("carousel", cap))
            return {"media_id": "c1", "permalink": "https://i/c1"}

        def fake_reel(url, cap, music=True):
            calls.append(("reel", cap))
            if fail["reel"]:
                raise RuntimeError("일시 오류")
            return {"media_id": "r1", "permalink": "https://i/r1", "audio_id": None}

        old = (instagram.publish_carousel, instagram.publish_reel, pub._safe_notify)
        instagram.publish_carousel, instagram.publish_reel, pub._safe_notify = fake_carousel, fake_reel, lambda t: None
        try:
            assert pub._publish(post, "test") is False
            fail["reel"] = False
            assert pub._publish(post, "retry") is True
        finally:
            instagram.publish_carousel, instagram.publish_reel, pub._safe_notify = old
        assert calls == [("carousel", "자세한 판"), ("reel", "짧은 판"), ("reel", "짧은 판")], calls
        assert post.meta["status"] == "published" and post.meta["instagram_carousel"]["media_id"] == "c1"


def test_music_falls_back_to_silent_when_no_facebook_token():
    from gongsi.publish import instagram
    os.environ.pop("FB_USER_TOKEN", None)
    assert not instagram.fb_ready()



def test_decks_full_first_then_short_with_valuation_card():
    """2026-10-29: 첫 편(신고서 단계)만 전체 덱, 경쟁률·상장 전날 편은 짧은 덱. 몸값 장은 모든 덱에"""
    from gongsi.models import Summary
    f = IpoFacts(**{**TNE, "lockup_ratio_pct": 29.52})
    s = Summary(business_lines=["터보블로워를 만듦", "수처리에 쓰임"], evidence=[])
    roles = {ph: [c.role for c in cards.build(f, s, date(2026, 10, 28), "x", phase=ph)[0]] for ph in ("pre", "post", "listing")}
    assert all("valuation" in r for r in roles.values()), roles
    assert len(roles["pre"]) > len(roles["post"]) and roles["post"] == ["hero", "valuation", "demand", "lockup", "schedule", "outro"], roles
    v = next(c for c in cards.build(f, s, date(2026, 10, 28), "x", phase="post")[0] if c.role == "valuation")
    assert v.title == "21,304원" and any("적정주가는 아니라고" in l for l in v.lines)


def test_lockup_ratio_ignores_later_total_rows_and_keeps_tiny_values():
    """2026-10-29: 확약 표 뒤 80줄 안의 다른 표 '합 계'를 잡아 0%로 읽던 문제(인텔리빅스 실제 6.14%),
    0.01% 미만을 0%로 반올림하던 문제(크리에이츠 5,000주 = 0.002%)"""
    from gongsi.adapters.dart_doc import parse
    lines = ["요약정보", "(다) 의무보유 확약기간별 수요예측 참여내역",
             "| 구 분 | 외국 기관투자자 | 합 계",
             "| 6개월 확약 | - | - | - | 16 | 21,397,000 | 9,000",
             "| 1개월 확약 | - | - | - | 14 | 15,340,000 | 9,000",
             "| 미확약 | 68 | 54,156,000 | 9,038 | 2,221 | 2,277,579,000 | 9,016",
             "| 합 계 | 70 | 55,603,000 | 9,055 | 2,251 | 2,314,316,000 | 9,016",
             "(라) 배정 내역", "| 구분 | 배정 | 비율", "| 기관 | 100 | 10", "| 합 계 | 100 | 100"]
    f = parse("\n".join(lines))
    assert f.lockup_ratio_pct == round((21397000 + 15340000) / 2314316000 * 100, 2), f.lockup_ratio_pct
    tiny = [l.replace("21,397,000", "5,000").replace("15,340,000", "-") for l in lines]
    assert parse("\n".join(tiny)).lockup_ratio_pct == 0.0002

if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for t in tests:
        t()
        print("ok", t.__name__)
    print(f"{len(tests)} passed")
