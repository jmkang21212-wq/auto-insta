"""유형 필터와 점수화. 규칙은 전부 여기 상수로 두고, 주간 로그를 보고 조정합니다."""
from __future__ import annotations

from dataclasses import dataclass

from gongsi.config import settings
from gongsi.models import Disclosure

ALLOWED_TYPES = {"ipo", "capital_increase", "cb", "bw", "major_holder", "insider"}

TYPE_WEIGHT = {
    "ipo": 10.0,
    "capital_increase": 6.0,
    "cb": 5.0,
    "bw": 5.0,
    "major_holder": 4.0,
    "insider": 3.0,
}

MARKET_WEIGHT = {"코스피": 2.0, "코스닥": 1.0}


@dataclass
class Scored:
    disclosure: Disclosure
    score: float
    selected: bool = False
    standalone: bool = False
    excluded_reason: str | None = None


def score(d: Disclosure) -> float:
    s = TYPE_WEIGHT.get(d.report_type, 0.0)
    s += MARKET_WEIGHT.get(d.market or "", 0.0)
    # 정정 공시는 원 공시보다 새 정보(확정가·경쟁률)를 담는 경우가 많아 가산
    if d.report_name and "정정" in d.report_name and d.report_type == "ipo":
        s += 1.0
    if d.report_name and "발행조건확정" in d.report_name:
        s += 2.0  # 경쟁률·확약·확정가가 들어 있는 단계
    # TODO: 금액 규모(공모·증자 금액), 시가총액(시세 출처 연결 후)
    return s


def select(disclosures: list[Disclosure]) -> list[Scored]:
    scored: list[Scored] = []
    # 같은 회사의 공모주 공시가 여러 건(원 신고서·기재정정·발행조건확정)이면 가장 최근 1건만 남긴다
    newest: dict[str, str] = {}
    for d in disclosures:
        if d.report_type == "ipo" and d.corp_code:
            newest[d.corp_code] = max(newest.get(d.corp_code, ""), d.rcept_no)
    for d in disclosures:
        if d.report_type == "ipo" and d.corp_code and newest.get(d.corp_code) != d.rcept_no:
            scored.append(Scored(d, 0.0, excluded_reason=f"superseded_by:{newest[d.corp_code]}"))
            continue
        if d.report_type not in ALLOWED_TYPES:
            scored.append(Scored(d, 0.0, excluded_reason=f"type_not_allowed:{d.report_type}"))
            continue
        scored.append(Scored(d, score(d)))

    candidates = sorted([s for s in scored if s.excluded_reason is None], key=lambda s: -s.score)
    if len(candidates) < settings.min_issues:
        return scored

    # 공모주는 각각 단독 편(청약 D-2 편성)이라 상한을 적용하지 않는다. 청약이 끝났는지는 추출 단계(원문 날짜)에서 거른다
    ipos = [s for s in candidates if s.disclosure.report_type == "ipo"]
    others = [s for s in candidates if s.disclosure.report_type != "ipo"]
    for s in ipos:
        s.selected = s.standalone = True
    for s in others[: settings.max_issues]:
        s.selected = True
    for s in others[settings.max_issues:]:
        s.excluded_reason = "below_cap"
    if others and (len(others) == 1 or others[0].score >= settings.standalone_ratio * others[1].score):
        others[0].standalone = True
    return scored
