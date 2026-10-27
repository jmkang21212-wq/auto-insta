"""캡션 규칙 시험. pytest로도, `python tests/test_caption.py` 로도 돌아갑니다.

캡션 모양(2026-10-27, ekke.now 참고): 카드는 훅, 자세한 내용은 캡션. 음슴체 정리 노트로
'-' 사실 / ':' 용어 풀이 / '→' 숫자가 뜻하는 것, 이모지 질문 소제목, 출처·면책, 고정 문구, 해시태그 5개.
줄 규칙: 덩어리 사이 빈 줄 하나, 휴대폰 한 줄 폭 안(긴 문장은 조사·쉼표 뒤에서 끊음), 날짜는 안 끊음.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gongsi import cards  # noqa: E402
from gongsi.models import IpoFacts, Summary  # noqa: E402
from gongsi.verify import check_caption_layout, check_forbidden, check_numbers  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]

# 실제 공시값: 동원파츠 [기재정정]증권신고서 20261023000331 (수요예측 전)
DONGWON = dict(corp_name="동원파츠", market="코스닥", industry="반도체 장비 부품", band_low_krw=23000, band_high_krw=26500,
               subscription_start="2026-11-02", subscription_end="2026-11-03", forecast_start="2026-10-22",
               forecast_end="2026-10-28", refund_date="2026-11-05", float_ratio_pct=29.47, offering_amount_krw=46690000000,
               underwriters=["삼성증권"], min_shares=20, deposit_rate_pct=50)  # 최소 20주, 증거금률 50%


def sample() -> IpoFacts:
    """가상 회사 샘플전자(수요예측 뒤). fixtures/sample_ipo.json"""
    data = json.loads((ROOT / "fixtures" / "sample_ipo.json").read_text(encoding="utf-8"))
    f = IpoFacts(**data["facts"]["20261026999001"])
    f.industry = f.industry or "반도체 광학 검사장비"
    return f


def body(cap: str) -> str:
    """숫자 대조 대상: 접수번호(출처 표기)와 위험 덩어리(원문 인용 + check_risks가 원문과 따로 대조)는 뺀다 (pipeline과 같게)"""
    blocks = [b for b in cap.split("\n\n") if not b.startswith("⚠️")]
    return re.sub(r"\d{14}", "", "\n\n".join(blocks))


def assert_clean(cap: str, facts: IpoFacts) -> None:
    assert check_caption_layout(cap) == [], check_caption_layout(cap)
    from datetime import date
    # pipeline과 같게: 카드를 만들며 남긴 계산값·비교값(derived)은 계산식·출처가 있으면 통과
    _, derived = cards.build(facts, Summary(business_lines=[], evidence=[]), date(2026, 10, 27), "x")
    bad = [p for p in check_numbers([body(cap)], facts) if not any(f"'{k}'" in p for k in derived)]
    assert bad == [], bad
    assert check_forbidden([body(cap)]) == []
    blocks = cap.split("\n\n")
    assert facts.corp_name in blocks[0].split("\n")[0]  # 첫 줄에 회사명 (검색·미리보기)
    assert blocks[-1].startswith("#") and blocks[-1].count("#") <= 5
    assert all(b.strip() for b in blocks)  # 빈 덩어리 없음
    assert " " not in cap  # 줄 나눔 방지용 공백은 결과에 남지 않음


def test_pre_forecast_real():
    f = IpoFacts(**DONGWON)
    cap = cards.caption(f, "20261023000331")
    assert_clean(cap, f)
    assert cap.split("\n")[0] == "🔔 동원파츠, 11월 2일 청약 열림"
    for want in ("- 청약은 11월 2일(월)~3일(화)", "- 상장일 유통 가능 물량은 29.47%", ": 상장 첫날부터 팔 수 있는 주식 비율을 뜻함",
                 "→ 10주 중 약 3주꼴로", "- 희망 공모가 23,000~26,500원", "💸 얼마 있어야 청약 가능?",
                 "- 최소 증거금 23만~26.5만원 (밴드 기준)", "- 청약은 삼성증권에서", "📊 경쟁률·확약은 언제 나옴?",
                 "→ 결과 나오면 경쟁률·확약 들고 다시 옴", "※ 투자 권유 아님", "🪄 청약 전에 볼 숫자"):
        assert want in cap, want
    assert "🏢 어떤 회사임?\n- 반도체 장비 부품 회사" in cap  # 제목에 업종이 안 들어가면 따로 씀


def test_post_forecast_sample():
    f = sample()
    cap = cards.caption(f, "20261026999001")
    assert_clean(cap, f)
    assert cap.split("\n")[0] == "📢 샘플전자 경쟁률 1,134.5:1 나옴"
    for want in ("- 의무보유 확약은 5.2%", "→ 나머지는 상장 첫날부터 팔 수 있음", "밴드 맨 위에서 정해짐", "🏢 어떤 회사임?",
                 "- 최소 증거금 21만원", "- 상장 예정일은 11월 12일(목)", "11월 3일(화)~4일(수)"):
        assert want in cap, want
    assert "결과 나오면" not in cap


def test_full_explanations_proceeds_risks_compare_credit():
    from gongsi.models import Risk, UseOfProceeds
    f = IpoFacts(**{**DONGWON, "use_of_proceeds": [UseOfProceeds(purpose="시설자금", amount_krw=30000000000),
                                                   UseOfProceeds(purpose="운영자금", amount_krw=10000000000)],
                    "risks": [Risk(category="회사위험", title="성장성 및 수익성 악화 위험", quote="원문")]})
    s = Summary(business_lines=[], evidence=[], risk_lines=[["업황이 나빠지면 매출이 줄 수 있음"]])
    cap = cards.caption(f, "20261023000331", s, credit="📷 자료 화면: Pixabay")
    assert_clean(cap, f)
    flat = " ".join(cap.split())  # 휴대폰 폭에 맞춰 끊긴 줄을 이어서 본다
    for want in ("💰 모은 돈은 어디에 씀? - 시설자금 75%, 운영자금 25%", "→ 시설자금은 공장·장비 사는 데 쓰는 돈임",
                 "⚠️ 회사가 직접 밝힌 위험은? - 성장성 및 수익성 악화 위험 : 업황이 나빠지면 매출이 줄 수 있음",
                 "→ 참고로 더본코리아 19.67%였음", "📷 자료 화면: Pixabay"):
        assert want in flat, want
    post = " ".join(cards.caption(sample(), "20261026999001").split())
    assert "→ 참고로 LG CNS 114:1, HD현대마린솔루션 201:1이었음" in post  # 비교 숫자는 기사 출처가 있는 것만


def test_long_names_and_text_wrap_inside_phone_width():
    f = IpoFacts(**{**DONGWON, "corp_name": "에이엔에이치스트럭쳐", "industry": "AI 기반 의료 영상 진단 솔루션",
                    "underwriters": ["한국투자증권", "NH투자증권", "KB증권"]})
    cap = cards.caption(f, "20261020000375")
    assert_clean(cap, f)
    assert "- 청약은 한국투자증권 외 2곳에서" in cap


def test_wrap_prefers_particles_and_keeps_dates():
    lines = cards.wrap(": 기관들이 써낸 주식 수가 배정 물량의 1,134.5배였다는 뜻")
    assert lines == [": 기관들이 써낸 주식 수가", "배정 물량의 1,134.5배였다는 뜻"]
    lines = cards.wrap("- 코스닥 상장 앞두고 " + cards.nb("11월 2일(월)~3일(화)") + " 일반 청약")
    assert any("11월 2일(월)~3일(화)" in l for l in lines)


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
    assert_clean(cards.caption(f, "20261020000375"), f)


def test_layout_check_catches_old_style():
    old = ("동원파츠 공모주 청약은 11월 2~3일이에요. 상장 첫날 팔 수 있는 물량은 29.47%예요\n\n"
           "청약 생각 있으세요? 댓글로 알려 주세요. 청약 전에 다시 보려면 저장해 두세요\n\n\n#공모주")
    problems = check_caption_layout(old)
    assert any("문장이 둘 이상" in p for p in problems)
    assert any("휴대폰 한 줄" in p for p in problems)
    assert any("빈 줄이 두 번" in p for p in problems)


def test_deposit_and_brokers():
    assert cards.man(230000) == "23만" and cards.man(265000) == "26.5만" and cards.man(1375000) == "137.5만"
    f = IpoFacts(**{**DONGWON, "underwriters": ["한국투자증권", "NH투자증권", "KB증권", "신한투자증권"]})
    assert cards.broker_text(f) == "한국투자증권 외 3곳"
    d: dict[str, str] = {}
    cards.deposit_text(f, d)
    assert "26.5" in d and "min_shares 20" in d["26.5"]
    assert cards.deposit_text(IpoFacts(**{**DONGWON, "deposit_rate_pct": None})) == ""  # 증거금률을 못 읽으면 줄을 안 씀


def test_investment_doc_name():
    assert cards.doc_kind("[기재정정]투자설명서") == "투자설명서"
    assert cards.doc_kind("[발행조건확정]증권신고서(지분증권)") == "증권신고서"
    assert "📄 DART 투자설명서 20261021000341" in cards.caption(sample(), "20261021000341", doc="투자설명서")


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for t in tests:
        t()
        print("ok", t.__name__)
    print(f"{len(tests)} passed")
