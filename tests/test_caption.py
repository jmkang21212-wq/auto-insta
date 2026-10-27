"""캡션 규칙 시험. pytest로도, `python tests/test_caption.py` 로도 돌아갑니다.

규칙(2026-10-27 '말투가 AI 같고 줄바꿈이 제멋대로' 피드백):
덩어리 사이 빈 줄 하나, 한 줄에 한 가지, 휴대폰 한 줄 폭 안, 숫자는 facts에서만, 해시태그 5개 이하.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gongsi import cards  # noqa: E402
from gongsi.models import IpoFacts  # noqa: E402
from gongsi.verify import check_caption_layout, check_forbidden, check_numbers  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]

# 실제 공시값: 동원파츠 [기재정정]증권신고서 20261023000331 (수요예측 전)
DONGWON = dict(corp_name="동원파츠", market="코스닥", industry="반도체 장비 부품", band_low_krw=23000, band_high_krw=26500,
               subscription_start="2026-11-02", subscription_end="2026-11-03", forecast_start="2026-10-22",
               forecast_end="2026-10-28", refund_date="2026-11-05", float_ratio_pct=29.47, offering_amount_krw=46690000000)


def sample() -> IpoFacts:
    """가상 회사 샘플전자(수요예측 뒤). fixtures/sample_ipo.json"""
    data = json.loads((ROOT / "fixtures" / "sample_ipo.json").read_text(encoding="utf-8"))
    f = IpoFacts(**data["facts"]["20261026999001"])
    f.industry = f.industry or "반도체 광학 검사장비"
    return f


def body(cap: str) -> str:
    return "\n".join(l for l in cap.splitlines() if not l.startswith("출처:"))


def assert_clean(cap: str, facts: IpoFacts) -> None:
    assert check_caption_layout(cap) == [], check_caption_layout(cap)
    assert check_numbers([body(cap)], facts) == [], check_numbers([body(cap)], facts)
    assert check_forbidden([body(cap)]) == []
    blocks = cap.split("\n\n")
    assert blocks[0].split("\n")[0] == f"{facts.corp_name} 공모주 청약"
    assert blocks[-1].startswith("#") and blocks[-1].count("#") <= 5
    assert all(b.strip() for b in blocks)  # 빈 덩어리 없음


def test_pre_forecast_real():
    f = IpoFacts(**DONGWON)
    cap = cards.caption(f, "20261023000331")
    assert_clean(cap, f)
    assert "11월 2일(월)~3일(화)" in cap
    assert "희망 공모가 23,000~26,500원" in cap
    assert "수요예측 10월 22일(목)~28일(수)" in cap
    assert "결과 나오면 다시 정리해서 올릴게요." in cap
    assert "댓글" not in cap and "저장" not in cap  # 질문·저장 유도는 카드 10장에만


def test_post_forecast_sample():
    f = sample()
    cap = cards.caption(f, "20261026999001")
    assert_clean(cap, f)
    assert "확정 공모가 21,000원 (밴드 맨 위)" in cap
    assert "수요예측 경쟁률 1,134.5:1" in cap
    assert "상장 예정일 11월 12일(목)" in cap
    assert "결과 나오면" not in cap


def test_band_position_outside_band():
    assert cards.band_pos(25000, 18000, 21000) == "밴드보다 위"
    assert cards.band_pos(17000, 18000, 21000) == "밴드보다 아래"
    assert cards.band_pos(20000, 18000, 21000) == "밴드 안"
    assert cards.band_pos(21000, 18000, 21000) == "밴드 맨 위"
    assert cards.band_pos(None, 18000, 21000) == ""


def test_dates_cross_month_and_single_day():
    assert cards.kdays("2026-10-30", "2026-11-02") == "10월 30일(금)~11월 2일(월)"
    assert cards.kdays("2026-11-02", None) == "11월 2일(월)"
    assert cards.kdays(None, None) == ""


def test_sparse_facts_no_blank_lines():
    f = IpoFacts(corp_name="에이엔에이치스트럭쳐", subscription_start="2026-11-27", subscription_end="2026-11-30")
    cap = cards.caption(f, "20261020000375")
    assert_clean(cap, f)
    assert "수요예측 결과" not in cap or "결과 나오면" in cap


def test_industry_ending_with_company_word():
    f = IpoFacts(**{**DONGWON, "industry": "바이오 기업"})
    assert "바이오 기업이에요." in cards.caption(f, "1")
    f = IpoFacts(**{**DONGWON, "industry": "AI 반도체 설계"})
    assert "AI 반도체 설계 회사예요." in cards.caption(f, "1")


def test_layout_check_catches_old_style():
    old = ("동원파츠 공모주 청약은 11월 2~3일이에요. 상장 첫날 팔 수 있는 물량은 29.47%예요\n\n"
           "청약 생각 있으세요? 댓글로 알려 주세요. 청약 전에 다시 보려면 저장해 두세요\n\n\n#공모주")
    problems = check_caption_layout(old)
    assert any("문장이 둘 이상" in p for p in problems)
    assert any("휴대폰 한 줄" in p for p in problems)
    assert any("빈 줄이 두 번" in p for p in problems)


def test_investment_doc_name():
    assert cards.doc_kind("[기재정정]투자설명서") == "투자설명서"
    assert cards.doc_kind("[발행조건확정]증권신고서(지분증권)") == "증권신고서"
    cap = cards.caption(sample(), "20261021000341", doc="투자설명서")
    assert "출처: DART 투자설명서 20261021000341" in cap


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for t in tests:
        t()
        print("ok", t.__name__)
    print(f"{len(tests)} passed")
    print("\n--- 동원파츠 (실제 공시, 수요예측 전) ---\n" + cards.caption(IpoFacts(**DONGWON), "20261023000331"))
    print("\n--- 샘플전자 (가상, 수요예측 뒤) ---\n" + cards.caption(sample(), "20261026999001"))
