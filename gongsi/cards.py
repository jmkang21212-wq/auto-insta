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

GLOSSARY = json.loads((settings.fixtures_dir / "glossary.json").read_text(encoding="utf-8"))
TOTAL = 10  # 최대 장수. 실제 장수는 build() 결과 길이 (수요예측 전엔 경쟁률·확약 장이 빠져 8장)
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


def build(facts: IpoFacts, summary: Summary, today: date | None = None, rcept_no: str = "") -> tuple[list[Card], dict[str, str]]:
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
    if facts.band_low_krw and facts.band_high_krw and facts.final_price_krw:
        pos = "밴드 맨 위" if facts.final_price_krw == facts.band_high_krw else ("밴드 맨 아래" if facts.final_price_krw == facts.band_low_krw else "밴드 안")
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

    # 값이 없어 뺀 장을 제외하고 번호를 다시 매기고, 각 장의 "다음 →" 문구는 실제 다음 장의 lead로 잇는다
    cards = [c for c in (c1, c2, c3, c4, c5, c6, c7, c8, c9, c10) if c is not None]
    for i, c in enumerate(cards, start=1):
        c.n = i
        c.tease = cards[i].lead if i < len(cards) else ""
        c.data["source"] = f"자료 금융감독원 전자공시 DART · 증권신고서 {rcept_no}".rstrip()
    return cards, derived


def caption(facts: IpoFacts, rcept_no: str) -> str:
    top = facts.revenue_mix[0] if facts.revenue_mix else None
    detail = []
    if top:
        detail.append(f"매출의 {pct(top.pct)}가 {top.name}" + (f", 고객은 {facts.customers}" if facts.customers else ""))
    if facts.major_holder_after_pct is not None:
        detail.append(f"최대주주 지분은 상장 후 {pct(facts.major_holder_after_pct)}")
    notes = [f"{u.purpose}: {u.note}" for u in facts.use_of_proceeds[:2] if u.note]
    return (
        f"{facts.corp_name}, 청약은 {mdrange(facts.subscription_start, facts.subscription_end)}이에요\n"
        + (f"공모가 {facts.final_price_krw:,}원" if facts.final_price_krw
           else (f"희망 공모가 {facts.band_low_krw:,}~{facts.band_high_krw:,}원" if facts.band_low_krw and facts.band_high_krw else "공모가 미정"))
        + f" · 공모 규모 {won(facts.offering_amount_krw)}" + ("" if facts.final_price_krw else " (밴드 하단 기준)") + "\n"
        + (f"상장은 {md(facts.listing_date)} 예정. 그날 시초가 결과로 다시 올게요\n\n" if facts.listing_date
           else "상장일은 아직 공시 전이에요. 정해지면 시초가 결과로 다시 올게요\n\n")
        + ("\n".join(detail + notes) + "\n\n" if (detail or notes) else "")
        + f"출처: DART 증권신고서 (접수번호 {rcept_no})\n"
        # 용어 풀이는 이번 카드에 실제로 나온 숫자만 (수요예측 전엔 경쟁률·확약 장이 없으니 설명도 뺀다)
        + "용어 풀이\n" + "".join(f"- {GLOSSARY[k]['caption']}\n" for k, v in
                                  (("demand", facts.demand_ratio), ("lockup", facts.lockup_ratio_pct), ("float", facts.float_ratio_pct))
                                  if v is not None) + "\n"
        f"공시에 적힌 사실만 전해드려요. 투자 권유는 아니에요.\n"
        f"#공모주 #청약 #{facts.corp_name} #공시"
    )
