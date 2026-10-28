"""어댑터와 코어 사이의 공통 스키마.

어댑터는 출처가 무엇이든 Disclosure(공시 메타)와 IpoFacts(구조화 필드)를 내놓고,
코어는 이 두 모델만 봅니다. 숫자 필드는 전부 API 또는 원문에서 온 값이어야 하며,
LLM이 채우는 필드는 Summary 하나뿐입니다.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class Disclosure(BaseModel):
    rcept_no: str
    corp_code: str | None = None
    corp_name: str
    report_type: str  # ipo / capital_increase / cb / insider / other
    report_name: str | None = None
    filed_at: str | None = None  # YYYY-MM-DD
    parent_rcept_no: str | None = None
    market: str | None = None  # 코스피 / 코스닥


class UseOfProceeds(BaseModel):
    purpose: str
    amount_krw: int  # 원 단위
    note: str | None = None  # 원문에 있는 세부 1줄 (LLM 아님)


class RevenueMix(BaseModel):
    name: str
    pct: float


class Risk(BaseModel):
    category: str  # 사업위험 / 회사위험 / 기타위험
    title: str = ""  # 원문 소제목 그대로 (예: "기술성장기업 상장요건 적용 위험")
    quote: str  # 원문 본문 앞부분 그대로. LLM 요약의 근거 대조용, 카드에는 직접 안 나감


class IpoFacts(BaseModel):
    """카드에 들어가는 모든 숫자의 출처. 값이 없으면 None으로 두고 카드에서는 '예정'으로 표기."""

    corp_name: str
    industry: str | None = None
    market: str | None = None
    homepage: str | None = None  # 신고서 표지의 홈페이지 주소 (로고 수집용)

    band_low_krw: int | None = None
    band_high_krw: int | None = None
    final_price_krw: int | None = None
    shares_offered: int | None = None
    offering_amount_krw: int | None = None

    demand_ratio: float | None = None  # 수요예측 경쟁률 (정정 후)
    lockup_ratio_pct: float | None = None  # 의무보유 확약 비율 (정정 후)
    float_ratio_pct: float | None = None  # 상장 직후 유통 가능 물량 비율
    major_holder_after_pct: float | None = None

    forecast_start: str | None = None
    forecast_end: str | None = None
    final_price_date: str | None = None
    subscription_start: str | None = None
    subscription_end: str | None = None
    refund_date: str | None = None
    listing_date: str | None = None

    underwriters: list[str] = Field(default_factory=list)
    min_shares: int | None = None
    deposit_rate_pct: int | None = None  # 일반청약 증거금률 (신고서 "청약증거금은 청약금액의 N%")
    min_deposit_krw: int | None = None

    fiscal_year: int | None = None  # revenue_krw·operating_income_krw의 결산연도
    revenue_krw: int | None = None
    operating_income_krw: int | None = None
    revenue_mix: list[RevenueMix] = Field(default_factory=list)
    customers: str | None = None  # 주요 고객사 이름 쉼표로 (증권신고서 원문 그대로)
    customer_note: str | None = None  # 매출 비중 문장 (예: 매출의 75% 이상이 삼성디스플레이에서 나옴)
    # 주관사가 매긴 몸값 (증권신고서 '인수인의 의견'). 신고서 스스로 '상장 뒤 적정주가 아님'이라고 밝히는 값
    valuation_krw: int | None = None  # 주당 평가가액
    valuation_discount_low_pct: float | None = None  # 평가액 대비 할인율 작은 쪽 (밴드 상단)
    valuation_discount_high_pct: float | None = None  # 큰 쪽 (밴드 하단)
    valuation_model: str | None = None  # PER, EV/EBITDA …
    valuation_multiple: float | None = None  # 적용 배수
    valuation_basis: str | None = None  # 적용 재무수치 원문
    valuation_basis_year: int | None = None
    # 상장 후 유통가능 주식 누적표 [{"months", "shares", "pct"}] (확약 해제편, 공모 주주 확약은 빠진 숫자)
    float_schedule: list[dict] = Field(default_factory=list)
    corp_code: str | None = None  # 후속편(상장 전날·확약 해제)에서 같은 회사 공시를 다시 찾을 때
    # 청약 결과 (증권발행실적보고서, 청약이 끝난 뒤에만): 일반투자자 배정·청약 수량, 건수, 금액, 균등 잔여 추첨 주식
    retail_alloc: int | None = None
    retail_qty: int | None = None
    retail_applicants: int | None = None
    retail_amount_krw: int | None = None
    even_lottery_shares: int | None = None
    result_rcept_no: str | None = None

    use_of_proceeds: list[UseOfProceeds] = Field(default_factory=list)
    risks: list[Risk] = Field(default_factory=list)

    # 원문에서 뽑은 문장들. LLM 요약의 근거 대조에만 쓰고 카드에는 직접 안 나감
    business_evidence: list[str] = Field(default_factory=list)
    glossary: dict[str, str] = Field(default_factory=dict)  # 신고서 전문용어 해설 표 (용어 → 뜻)

    raw: dict = Field(default_factory=dict)  # API 응답 원본 (fact.fields에 보관)

    def number_set(self) -> set[str]:
        """검증용: 카드 문구에 등장할 수 있는 모든 숫자 표기를 모읍니다."""
        nums: set[str] = set()

        def add(v):
            if v is None:
                return
            if isinstance(v, float):
                nums.add(f"{v:g}")
                nums.add(f"{v:.1f}")
                if v.is_integer():
                    nums.add(str(int(v)))
            elif isinstance(v, int):
                v = abs(v)  # 영업손실은 카드에서 양수로 표기
                nums.add(str(v))
                nums.add(f"{v:,}")
                for unit, div in (("억", 100_000_000), ("만", 10_000)):
                    if v >= div and v % div == 0:
                        nums.add(f"{v // div:,}")
                        nums.add(str(v // div))
                if v >= 100_000_000:  # cards.won(): 414.2억 처럼 소수 1자리 억 표기
                    nums.add(f"{v / 100_000_000:,.1f}")
                    nums.add(f"{v / 100_000_000:.1f}")
            elif isinstance(v, str):
                for tok in _date_tokens(v):
                    nums.add(tok)

        for name, val in self.model_dump().items():
            if name in ("raw", "business_evidence", "risks", "glossary"):
                continue
            if isinstance(val, list):
                for item in val:
                    if isinstance(item, dict):
                        for vv in item.values():
                            add(vv)
                    else:
                        add(item)
            else:
                add(val)
        return nums


def _date_tokens(s: str) -> list[str]:
    """'2026-11-12' → ['2026-11-12', '11.12', '11/12', '12']"""
    import re

    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", s)
    if not m:
        return []
    y, mo, d = m.groups()
    return [s, f"{mo}.{d}", f"{int(mo)}.{int(d)}", f"{mo}/{d}", str(int(d)), d, f"{int(mo)}", mo]


class Summary(BaseModel):
    """LLM이 쓰는 유일한 부분. 문장마다 원문 근거 문장을 붙입니다."""

    business_lines: list[str]  # 2장 본문, 최대 2줄
    evidence: list[str]  # 각 문장에 대응하는 원문 문장 (business_evidence 중 하나)
    risk_lines: list[list[str]] = []  # 8장: 위험별 쉬운 말 한 문단(60~80자). 숫자는 해당 Risk.quote 안에 있는 것만 (비면 소제목만 표시)
