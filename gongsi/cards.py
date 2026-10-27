"""캐러셀 10장을 facts + summary로 채웁니다.

원칙: 한 장에 숫자 하나. 각 장은 제목 + (그래픽) + 설명 한 줄 + 비교 한 줄까지. 그 이상은 캡션으로 보낸다.
문구에 들어가는 숫자는 전부 facts에서 오고, 계산값·비교값은 derived에 출처와 함께 남겨 검증 단계가 대조한다.
verbatim=True 텍스트(원문 인용)는 숫자 대조에서 뺀다.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date

from gongsi.benchmarks import compare_float, compare_lockup, compare_ratio, refs as bench_refs
from gongsi.config import settings
from gongsi.models import IpoFacts, Summary
from gongsi.verify import CAPTION_LINE_MAX, line_width

GLOSSARY = json.loads((settings.fixtures_dir / "glossary.json").read_text(encoding="utf-8"))
BRAND = json.loads((settings.fixtures_dir / "brand.json").read_text(encoding="utf-8"))
TOTAL = 11  # 최대 장수(브랜드 홍보 장 포함). 실제 장수는 build() 결과 길이 (수요예측 전엔 경쟁률·확약 장이 빠져 9장)
# 8장 분류 꼬리표: 신고서 용어(사업위험·회사위험·기타위험)를 풀어 쓴 고정 문구. 인용문 자체는 원문 그대로
RISK_LABEL = {"사업위험": "업계 전체가 안고 있는 위험", "회사위험": "이 회사만의 위험", "기타위험": "그 밖에 알아둘 위험"}


def compare_rows(corp: str, value: float | None, label: str, metric: str, derived: dict[str, str]) -> list[dict]:
    """비교 막대: 이 회사(hero) + 유명 공모주 2곳. 막대 길이는 최댓값 기준 비율."""
    if value is None:
        return []
    rows = [{"name": corp, "value": float(value), "label": label, "hero": True, "when": ""}] + bench_refs(metric, derived)
    top = max(r["value"] for r in rows) or 1
    for r in rows:
        r["w"] = max(2, round(r["value"] / top * 100))
    return rows


def won(v: int | None) -> str:
    if v is None:
        return "미정"
    if v >= 100_000_000 and v % 100_000_000 == 0:
        return f"{v // 100_000_000:,}억"
    if v >= 100_000_000:
        return f"{v / 100_000_000:,.1f}억"
    if v >= 10_000 and v % 10_000 == 0:
        return f"{v // 10_000:,}만원"
    return f"{v:,}원"


def md(d: str | None) -> str:
    return "미정" if not d else f"{d[5:7]}.{d[8:10]}"


def mdrange(a: str | None, b: str | None) -> str:
    if not a:
        return "미정"
    if not b or a == b:
        return md(a)
    return f"{md(a)}~{b[8:10]}" if a[5:7] == b[5:7] else f"{md(a)}~{md(b)}"


def pct(v: float | None) -> str:
    return "미정" if v is None else f"{v:g}%"


def ratio(v: float | None) -> str:
    return "미정" if v is None else f"{v:,.1f}:1"


@dataclass
class Card:
    n: int
    kind: str  # image / compare / metric / proceeds / checklist / schedule / closing
    title: str
    sub: str = ""
    lines: list[str] = field(default_factory=list)
    tease: str = ""  # 이 장 맨 아래 "다음 장으로" 문구. build()가 다음 장의 lead로 채운다
    data: dict = field(default_factory=dict)
    verbatim: list[str] = field(default_factory=list)
    role: str = ""  # hero / about / float / demand / lockup / proceeds / checklist / risk / schedule / closing (사진·번호는 role 기준)
    lead: str = ""  # 앞 장에서 이 장을 예고하는 문구

    def texts(self) -> list[str]:
        extra = [str(v) for k, v in self.data.items() if isinstance(v, str) and k not in ("source", "badge")]
        extra += [it.get("why", "") for it in self.data.get("checks", [])]
        return [self.title, self.sub, *self.lines, self.tease] + extra


def doc_kind(report_name: str | None) -> str:
    """출처 줄에 쓸 문서 이름. 접수번호가 투자설명서면 증권신고서라고 쓰지 않는다."""
    return "투자설명서" if "투자설명서" in (report_name or "") else "증권신고서"


def build(facts: IpoFacts, summary: Summary, today: date | None = None, rcept_no: str = "",
          doc: str = "증권신고서") -> tuple[list[Card], dict[str, str]]:
    today = today or date.today()
    derived: dict[str, str] = {}
    dday = (date.fromisoformat(facts.subscription_start) - today).days if facts.subscription_start else None
    badge = ("오늘 청약" if dday == 0 else f"청약 D-{dday}") if dday is not None and dday >= 0 else "청약 일정 확인"
    has_demand = facts.demand_ratio is not None and facts.lockup_ratio_pct is not None

    # 1 훅
    hook = (f"경쟁률은 {ratio(facts.demand_ratio)}인데\n확약은 {pct(facts.lockup_ratio_pct)}예요" if has_demand
            else f"{won(facts.offering_amount_krw)}을 모으러\n상장에 나섰어요")
    c1 = Card(1, "image", facts.corp_name,
              f"{facts.industry or ''} · {facts.market or ''} · 청약 {mdrange(facts.subscription_start, facts.subscription_end)}".strip(" ·"),
              data={"badge": badge, "hook": hook}, role="hero")

    # 2 정체: 요약 2줄 + 실적 1줄
    fin = ""
    if facts.revenue_krw:
        when = "작년" if facts.fiscal_year == today.year - 1 else (f"{facts.fiscal_year}년" if facts.fiscal_year else "최근 결산")
        oi = facts.operating_income_krw
        fin = f"{when} 매출은 {won(facts.revenue_krw)}" + (
            (f", 영업이익은 {won(oi)}이에요" if oi > 0 else f", 영업손실은 {won(-oi)}이에요") if oi else "이에요")
    c2 = Card(2, "image", "\n".join(summary.business_lines), lines=[fin] if fin else [],
              data={"badge": badge}, role="about", lead="무슨 회사인지부터 볼게요 →")

    # 3 유통 가능 물량
    c3 = Card(3, "compare", pct(facts.float_ratio_pct), "상장하자마자 팔 수 있는 물량이",
              lines=[GLOSSARY["float"]["card"]] if facts.float_ratio_pct is not None else [],
              data={"badge": badge, "legend": "유명 공모주와 비교",
                    "compare": compare_rows(facts.corp_name, facts.float_ratio_pct, pct(facts.float_ratio_pct), "float_pct", derived)},
              role="float", lead="그런데 눈에 띄는 숫자가 하나 있어요 →")

    # 4 경쟁률 · 5 확약: 수요예측 전이라 값이 없으면 장 자체를 뺀다 ("미정" 카드는 올리지 않는다, 2026-10-27 결정)
    c4 = c5 = None
    if facts.demand_ratio is not None:
        c4 = Card(4, "compare", ratio(facts.demand_ratio), "기관 수요예측 경쟁률은",
                  lines=[GLOSSARY["demand"]["card"].format(ratio=f"{facts.demand_ratio:,.0f}")],
                  data={"badge": badge, "legend": "유명 공모주와 비교",
                        "compare": compare_rows(facts.corp_name, facts.demand_ratio, ratio(facts.demand_ratio), "demand_ratio", derived)},
                  role="demand", lead="기관들은 얼마나 몰렸을까요? →")
        derived[f"{facts.demand_ratio:,.0f}"] = f"round(demand_ratio {facts.demand_ratio})"
    if facts.lockup_ratio_pct is not None:
        c5 = Card(5, "compare", pct(facts.lockup_ratio_pct), "기관이 당분간 안 팔기로 한 비율은",
                  lines=[GLOSSARY["lockup"]["card"].format(lock=pct(facts.lockup_ratio_pct))],
                  data={"badge": badge, "legend": "유명 공모주와 비교",
                        "compare": compare_rows(facts.corp_name, facts.lockup_ratio_pct, pct(facts.lockup_ratio_pct), "lockup_pct", derived)},
                  role="lockup", lead="그런데 안 팔기로 한 비율은 →")

    # 6 돈의 행방
    uses = facts.use_of_proceeds[:3]
    total = facts.offering_amount_krw or sum(u.amount_krw for u in uses) or 1
    top_pct = round(uses[0].amount_krw / total * 100) if uses else 0
    if uses:
        derived[str(top_pct)] = f"round({uses[0].amount_krw} / {total} * 100)"
    c6 = Card(6, "proceeds", f"{top_pct}%는 {uses[0].purpose}에 써요" if uses else "자금 사용 목적",
              f"{won(facts.offering_amount_krw)}을 모으면",
              lines=[GLOSSARY["proceeds"].get(uses[0].purpose.replace(" ", ""), GLOSSARY["proceeds"]["_default"])] if uses else [],
              data={"badge": badge,
                    "bars": [{"name": u.purpose, "amt": won(u.amount_krw), "w": round(u.amount_krw / uses[0].amount_krw * 100)} for u in uses]},
              role="proceeds", lead="모은 돈은 어디에 쓸까요? →")

    # 7 체크리스트
    if facts.lockup_ratio_pct is not None:
        first = {"label": "의무보유 확약", "value": pct(facts.lockup_ratio_pct), "why": GLOSSARY["checks"]["lockup"]}
    elif facts.band_low_krw and facts.band_high_krw:  # 수요예측 전엔 확약 대신 밴드와 확정일
        first = {"label": "희망 공모가 밴드", "value": f"{facts.band_low_krw:,}~{facts.band_high_krw:,}원",
                 "why": GLOSSARY["checks"].get("band", "확정가는 수요예측이 끝난 뒤 정정 공시로 나와요. 밴드 위에서 정해지기도 해요")}
    else:
        first = {"label": "공모 규모", "value": won(facts.offering_amount_krw), "why": "이번 공모로 회사가 모으려는 돈이에요"}
    checks = [first,
              {"label": "상장 직후 유통 가능 물량", "value": pct(facts.float_ratio_pct), "why": GLOSSARY["checks"]["float"]},
              # 확정가 전에는 최소 증거금을 계산할 수 없다 (밴드로 추정하지 않음). 그땐 환불일만, 설명도 금액을 가리키지 않게
              ({"label": "최소 청약 증거금 · 환불일", "value": f"{facts.min_deposit_krw:,}원 · {md(facts.refund_date)}",
                "why": GLOSSARY["checks"]["deposit"]} if facts.min_deposit_krw else
               {"label": "증거금 환불일", "value": md(facts.refund_date),
                "why": GLOSSARY["checks"].get("refund", "청약 때 낸 증거금 중 배정받지 못한 돈은 이날 돌려받아요")})]
    checks = [c for c in checks if "미정" not in c["value"]]  # 값이 없는 항목은 싣지 않는다
    deposit_text(facts, derived)  # 캡션의 최소 증거금(만 원 단위·밴드 범위) 계산식을 검증용으로 남긴다
    c7 = Card(7, "checklist", f"청약 전, 이 {len(checks)}개만 확인하세요", data={"badge": badge, "checks": checks},
              role="checklist", lead="청약 전에 딱 몇 개만 확인해요 →") if checks else None

    # 8 위험 (원문 인용)
    # 소제목은 원문 그대로(verbatim), 본문 2줄은 LLM이 쉬운 말로 바꾼 것. 숫자 대조는 verify.check_risks가 원문과 한다
    risk_lines = summary.risk_lines if len(summary.risk_lines) == len(facts.risks[:2]) else [[] for _ in facts.risks[:2]]
    c8 = Card(8, "image", "회사가 신고서에\n직접 쓴 위험이에요",
              data={"badge": badge, "risks": [{"category": r.category, "label": RISK_LABEL.get(r.category, r.category),
                                               "title": r.title or r.quote[:40], "lines": lines}
                                              for r, lines in zip(facts.risks[:2], risk_lines)]},
              verbatim=[r.title for r in facts.risks[:2]], role="risk", lead="회사가 직접 말한 위험도 있어요 →") if facts.risks else None

    # 9 일정
    if facts.final_price_krw:
        price = f"공모가 {facts.final_price_krw:,}원"
    elif facts.band_low_krw and facts.band_high_krw:
        price = f"희망 공모가 {facts.band_low_krw:,}~{facts.band_high_krw:,}원"
    else:
        price = "공모가 미정"
    pos = band_pos(facts.final_price_krw, facts.band_low_krw, facts.band_high_krw)  # 밴드 밖이면 '밴드보다 위/아래'
    if pos:
        price += f" ({pos})"
    c9 = Card(9, "schedule", mdrange(facts.subscription_start, facts.subscription_end), "청약은 이날이에요",
              lines=[" · ".join(x for x in (", ".join(facts.underwriters), price) if x)],
              data={"badge": badge, "timeline": [pt for pt in [
                  {"label": "확정가", "date": md(facts.final_price_date)},
                  {"label": "청약", "date": mdrange(facts.subscription_start, facts.subscription_end), "main": True},
                  {"label": "환불", "date": md(facts.refund_date)},
                  {"label": "상장", "date": md(facts.listing_date)},
              ] if pt["date"] != "미정"]},  # 날짜 없는 점은 타임라인에서 뺀다
              role="schedule", lead="그래서, 청약은 언제일까요? →")

    # 10 마무리: 질문 + 저장
    if has_demand and facts.float_ratio_pct is not None:
        title10, ask = "이 조합, 어떻게 보세요?", GLOSSARY["ask"].format(lock=pct(facts.lockup_ratio_pct), float=pct(facts.float_ratio_pct))
    elif facts.float_ratio_pct is not None:
        title10, ask = "청약, 생각 있으세요?", GLOSSARY.get("ask_pre", "유통 물량 {float}. 수요예측 결과 나오면 다시 볼게요. 청약 생각 있으세요? 댓글로 남겨 주세요").format(float=pct(facts.float_ratio_pct))
    else:
        title10, ask = "청약, 생각 있으세요?", ""
    c10 = Card(10, "closing", title10, lines=[ask] if ask else [],
               data={"badge": badge, "cta": (f"상장일 {md(facts.listing_date)}에 공모가 대비 시초가 결과로 다시 올게요" if facts.listing_date
                                             else "상장일이 정해지면 공모가 대비 시초가 결과로 다시 올게요"),
                     "footer": "공시에 적힌 사실만 전해드려요. 투자 권유는 아니에요"},
               role="closing", lead="마지막으로 하나만 여쭤볼게요 →")

    # 11 브랜드 홍보 장: 문구는 전부 fixtures/brand.json (숫자 없음). 아이디는 정해졌을 때만 표시
    c11 = Card(11, "outro", BRAND["name"], data={"brand": BRAND}, role="outro")

    # 값이 없어 뺀 장을 제외하고 번호를 다시 매기고, 각 장의 "다음 →" 문구는 실제 다음 장의 lead로 잇는다
    cards = [c for c in (c1, c2, c3, c4, c5, c6, c7, c8, c9, c10, c11) if c is not None]
    for i, c in enumerate(cards, start=1):
        c.n = i
        c.tease = cards[i].lead if i < len(cards) else ""
        c.data["source"] = f"자료 금융감독원 전자공시 DART · {doc} {rcept_no}".rstrip()
    return cards, derived


WEEKDAY = "월화수목금토일"


def kdate(d: str) -> str:
    """'2026-11-02' → '11월 2일(월)' (캡션용. 요일은 날짜에서 계산하므로 공시 숫자가 아님)"""
    x = date.fromisoformat(d)
    return f"{x.month}월 {x.day}일({WEEKDAY[x.weekday()]})"


def kdays(a: str | None, b: str | None) -> str:
    """'2026-11-02','2026-11-03' → '11월 2일(월)~3일(화)', 달이 바뀌면 '10월 30일(금)~11월 2일(월)'"""
    if not a:
        return ""
    if not b or a == b:
        return kdate(a)
    x, y = date.fromisoformat(a), date.fromisoformat(b)
    return f"{kdate(a)}~" + (f"{y.day}일({WEEKDAY[y.weekday()]})" if (x.year, x.month) == (y.year, y.month) else kdate(b))


def band_pos(final: int | None, low: int | None, high: int | None) -> str:
    """확정 공모가가 희망 밴드의 어디인지. 밴드 밖(상단 초과·하단 미만)도 사실대로 쓴다."""
    if not (final and low and high):
        return ""
    if final > high:
        return "밴드보다 위"
    if final == high:
        return "밴드 맨 위"
    if final < low:
        return "밴드보다 아래"
    if final == low:
        return "밴드 맨 아래"
    return "밴드 안"


def man(v: int) -> str:
    """230000 → '23만', 265000 → '26.5만' (만 원 단위. 증거금처럼 사람이 '몇 만 원'으로 읽는 금액용)"""
    return f"{v / 10_000:,g}만"


def deposit_text(facts: IpoFacts, derived: dict[str, str] | None = None) -> str:
    """최소 청약 증거금 한 줄. 확정가가 있으면 그 값, 없으면 희망 밴드 양 끝으로 계산한 범위('밴드 기준'이라고 밝힘).
    다른 공모주 글들이 청약 전에 꼭 넣는 항목이라 캡션에 싣는다(2026-10-27). derived를 주면 계산식을 남긴다."""
    if facts.min_deposit_krw:
        s = man(facts.min_deposit_krw)
        if derived is not None:
            derived[s[:-1]] = f"min_deposit {facts.min_deposit_krw:,} / 10,000"
        return f"최소 증거금 {s}원"
    if facts.min_shares and facts.deposit_rate_pct and facts.band_low_krw and facts.band_high_krw:
        ends = [facts.min_shares * p * facts.deposit_rate_pct // 100 for p in (facts.band_low_krw, facts.band_high_krw)]
        if derived is not None:
            for v, p in zip(ends, (facts.band_low_krw, facts.band_high_krw)):
                derived[man(v)[:-1]] = f"min_shares {facts.min_shares} * band {p:,} * {facts.deposit_rate_pct}% = {v:,}"
        return f"최소 증거금 {man(ends[0])}~{man(ends[1])}원 (밴드 기준)"
    return ""


def broker_text(facts: IpoFacts) -> str:
    """청약 증권사: 신고서 공모개요의 대표주관·공동주관·인수회사 (일반청약은 이 증권사들에서 받는다)"""
    u = facts.underwriters
    if not u:
        return ""
    return ", ".join(u) if len(u) <= 2 else f"{u[0]} 외 {len(u) - 1}곳"


def _batchim(word: str) -> bool:
    ch = word[-1]
    return "가" <= ch <= "힣" and (ord(ch) - 0xAC00) % 28 != 0


def _fit(*candidates: str) -> str:
    """휴대폰 한 줄에 들어가는 첫 후보. 회사명이 길면 뒤쪽의 짧은 문구를 쓴다"""
    cands = [c for c in candidates if c]
    return next((c for c in cands if line_width(c) <= CAPTION_LINE_MAX), cands[-1])


NB = "\u00a0"  # 줄을 나누면 안 되는 공백 (날짜 '11월 2일(월)' 같은 묶음 안)
_GOOD_END = (",", "고", "며", "면", "서", "는", "은", "이", "가", "을", "를", "에", "로", "의", "와", "과", "도", "만", "라", "지", "께")


def nb(s: str) -> str:
    return s.replace(" ", NB)


def wrap(text: str) -> list[str]:
    """휴대폰 한 줄 폭에 맞게 어절 단위로 끊는다. ekke 캡션처럼 구 단위로 일부러 끊은 모양이 되게
    조사·쉼표 뒤에서 끊는 쪽을 고르고, 그중 두 줄 폭이 비슷한 자리를 쓴다. 날짜 같은 묶음(NB)은 안 끊는다."""
    out = lambda lines: [l.replace(NB, " ") for l in lines]  # noqa: E731
    if line_width(text) <= CAPTION_LINE_MAX:
        return out([text])
    words = text.split(" ")
    best = None
    for i in range(1, len(words)):
        a, b = " ".join(words[:i]), " ".join(words[i:])
        if line_width(a) <= CAPTION_LINE_MAX and line_width(b) <= CAPTION_LINE_MAX:
            score = max(line_width(a), line_width(b)) + (0 if words[i - 1].endswith(_GOOD_END) else 3)
            if best is None or score < best[0]:
                best = (score, [a, b])
    if best:
        return out(best[1])
    lines, cur = [], ""
    for w in words:
        cand = f"{cur} {w}".strip()
        if cur and line_width(cand) > CAPTION_LINE_MAX:
            lines.append(cur)
            cur = w
        else:
            cur = cand
    return out(lines + [cur])


# 캡션 말투(fixtures/brand.json voice): ekke.now 계정을 참고한 음슴체 정리 노트.
# 제목 한 줄로 훅 → '-' 사실, ':' 처음 보는 용어 풀이, '→' 숫자가 뜻하는 것 → 이모지 질문 소제목 → 출처 → 고정 문구
# 풀이·해석 문장은 고정 문장(LLM 아님)이고 좋다·나쁘다는 쓰지 않는다
EXPLAIN = {
    "float": ": 상장 첫날부터 팔 수 있는 주식 비율을 뜻함",
    "demand": ": 기관들이 써낸 주식 수가 배정 물량의 {x}배였다는 뜻",
    "lockup": ": 기관이 받은 주식을 일정 기간 안 팔겠다고 약속한 비율임",
    "band": ": 밴드란 회사가 처음 내건 희망 공모가 범위({band})를 뜻함",
    "equal": ": 균등 배정은 최소 수량 이상 넣은 사람끼리 똑같이 나누는 방식임",
    "deposit_range": ": 공모가가 밴드 어디서 정해지느냐에 따라 달라짐",
    "forecast": ": 수요예측이란 기관들이 원하는 가격과 수량을 미리 써내는 절차임",
}


def caption(facts: IpoFacts, rcept_no: str, summary: Summary | None = None, doc: str = "증권신고서") -> str:
    """인스타 캡션. 카드(또는 릴스)는 훅, 자세한 내용은 캡션에서 (ekke.now 방식, 2026-10-27 사용자 제안).

    2026-10-27 피드백을 거쳐 지금 모양이 됐다.
    1) '말투가 AI 같고 줄바꿈이 제멋대로' → 휴대폰 한 줄 폭, 덩어리 사이 빈 줄 하나
    2) '다른 글 참고' → 청약 증권사·최소 증거금 같은 청약 실무 숫자
    3) '차분하다, 후킹하라, ekke 참고, 해석과 모를 만한 용어 설명을 넣어라' → 음슴체 정리 노트,
       '-' 사실 / ':' 용어 풀이 / '→' 숫자가 뜻하는 것, 이모지 질문 소제목, 고정 문구로 마무리
    지키는 선: 추천·확신·상승 암시 금지(verify.FORBIDDEN, 🚀📈 안 씀), 숫자는 전부 facts·derived에서,
    구현 안 된 약속 금지, 해시태그 5개까지(2025.12~). 줄 규칙은 verify.check_caption_layout이 검사
    """
    corp = facts.corp_name
    has_band = bool(facts.band_low_krw and facts.band_high_krw)
    start = date.fromisoformat(facts.subscription_start) if facts.subscription_start else None
    days = nb(kdays(facts.subscription_start, facts.subscription_end)) if start else ""
    band = f"{facts.band_low_krw:,}~{facts.band_high_krw:,}원" if has_band else ""
    brokers = broker_text(facts)
    blocks: list[list[str]] = []

    def item(sign: str, text: str) -> list[str]:
        return wrap(f"{sign} {text}")

    # 1. 제목 + 핵심
    first: list[str] = []
    if facts.demand_ratio is not None:  # 수요예측 뒤
        first.append(_fit(f"📢 {corp} 경쟁률 {ratio(facts.demand_ratio)} 나옴", f"📢 {corp} 수요예측 결과 나옴"))
        first += item("-", f"기관 경쟁률 {ratio(facts.demand_ratio)}")
        first += wrap(EXPLAIN["demand"].format(x=f"{facts.demand_ratio:,g}"))
        if facts.lockup_ratio_pct is not None:
            first += item("-", f"의무보유 확약은 {pct(facts.lockup_ratio_pct)}")
            first += wrap(EXPLAIN["lockup"])
            first += item("→", "나머지는 상장 첫날부터 팔 수 있음")
        if facts.final_price_krw:
            pos = band_pos(facts.final_price_krw, facts.band_low_krw, facts.band_high_krw)
            first += item("-", f"확정 공모가 {facts.final_price_krw:,}원" + (f", {pos}에서 정해짐" if pos else ""))
            if pos:
                first += wrap(EXPLAIN["band"].format(band=band))
    else:  # 수요예측 전
        when = f"{start.month}월 {start.day}일" if start else ""
        ind = (facts.industry or "").strip()
        first.append(_fit(f"🔔 {ind} {corp}, {when} 청약 열림" if ind and when else "",
                          f"🔔 {corp}, {when} 청약 열림" if when else "", f"🔔 {corp} 공모주 청약 열림"))
        if days:
            first += item("-", f"{facts.market + ' ' if facts.market else ''}상장 앞두고 {days} 일반 청약")
        if facts.float_ratio_pct is not None:
            first += item("-", f"상장일 유통 가능 물량은 {pct(facts.float_ratio_pct)}")
            first += wrap(EXPLAIN["float"])
            n = round(facts.float_ratio_pct / 10)
            if n:
                first += item("→", f"10주 중 약 {n}주꼴로 첫날 시장에 나올 수 있다는 얘기")
        if has_band:
            first += item("-", f"희망 공모가 {band}")
    blocks.append(first)

    # 2. 어떤 회사인지 (업종 + 신고서 실적)
    who: list[str] = []
    if facts.industry:
        ind = facts.industry.strip()
        who += item("-", ind if ind.endswith(("회사", "기업", "업체")) else f"{ind} 회사")
    if facts.revenue_krw:
        yr = f"{facts.fiscal_year}년 " if facts.fiscal_year else ""
        oi = facts.operating_income_krw
        tail = (f", 영업이익 {won(oi)}" if oi > 0 else f", 영업손실 {won(-oi)}") if oi else ""
        who += item("-", f"{yr}매출 {won(facts.revenue_krw)}{tail}")
    ind_in_title = bool(facts.industry) and first[0].startswith(f"🔔 {facts.industry.strip()} ")
    if who and not (ind_in_title and not facts.revenue_krw):  # 업종만 있고 이미 제목에 있으면 겹치니 생략
        blocks.append(["🏢 어떤 회사임?"] + who)

    # 3. 청약 실무: 증거금, 증권사, 일정
    money = ["💸 얼마 있어야 청약 가능?"]
    dep = deposit_text(facts)
    if dep:
        money += item("-", dep)
        if facts.min_deposit_krw is None:
            money += wrap(EXPLAIN["deposit_range"])
        money += wrap(EXPLAIN["equal"])
    if brokers:
        money += item("-", f"청약은 {brokers}에서" + (f", {days}" if days and facts.demand_ratio is not None else ""))
    if facts.refund_date:
        money += item("-", f"증거금 환불은 {nb(kdate(facts.refund_date))}")
    if facts.listing_date:
        money += item("-", f"상장 예정일은 {nb(kdate(facts.listing_date))}")
    if len(money) > 1:
        blocks.append(money)

    # 4. 수요예측 전: 경쟁률·확약은 언제 나오는지 (발행조건확정 공시가 나오면 2차 게시가 실제로 돈다)
    if facts.demand_ratio is None:
        nxt = ["📊 경쟁률·확약은 언제 나옴?"]
        if facts.forecast_end:
            nxt += item("-", f"수요예측이 {nb(kdate(facts.forecast_end))}까지라 그 뒤에 나옴")
        nxt += wrap(EXPLAIN["forecast"]) + item("→", "결과 나오면 경쟁률·확약 들고 다시 옴")
        blocks.append(nxt)

    blocks.append([f"📄 DART {doc} {rcept_no}", "※ 투자 권유 아님, 판단은 각자 몫"])
    handle = BRAND.get("handle", "")
    blocks.append([f"🪄 청약 전에 볼 숫자, 제일 먼저 받아보기{' @' + handle if handle else ''}"])
    tags = ["#공모주", "#공모주청약", "#" + corp.replace(" ", ""), "#청약일정", BRAND.get("hashtag", "")]
    blocks.append([" ".join([t for t in dict.fromkeys(tags) if t and t != "#"][:5])])
    blocks = [[l for l in b if l] for b in blocks]  # 값이 없어 비는 줄은 빼서 덩어리 안에 빈 줄이 생기지 않게
    return "\n\n".join("\n".join(b) for b in blocks if b)
