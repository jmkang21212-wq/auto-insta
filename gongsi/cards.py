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
    return "청약 증권사 " + ", ".join(u[:2]) + (f" 외 {len(u) - 2}곳" if len(u) > 2 else "")


def _ieyo(word: str) -> str:
    """받침 있으면 '이에요', 없으면 '예요' (한글이 아닌 끝 글자는 '예요')"""
    ch = word[-1]
    return "이에요" if "가" <= ch <= "힣" and (ord(ch) - 0xAC00) % 28 else "예요"


def caption(facts: IpoFacts, rcept_no: str, summary: Summary | None = None, doc: str = "증권신고서") -> str:
    """인스타 캡션. 2026-10-27 '말투가 AI 같고 줄바꿈이 제멋대로' 피드백으로 다시 짰고,
    같은 날 다른 공모주 글(알파스퀘어 인사이트, 글래스월렛, 토스뱅크)을 참고해 고쳤다.
    - 다른 글들은 제목에 숫자 하나로 훅을 건다("경쟁률 868대 1인데 확약은 4%"). → 둘째 줄을 훅 숫자로
    - 다른 글들은 청약 실무 숫자(청약 증권사, 최소 증거금)를 꼭 넣는다. → 숫자 덩어리에 추가
    - 훅에 쓴 숫자는 아래에서 다시 쓰지 않는다

    규칙 (verify.check_caption_layout이 검사):
    - 덩어리(제목 / 숫자 / 일정 / 출처 / 해시태그) 사이에만 빈 줄 하나. 덩어리 안에는 빈 줄 없음
    - 한 줄에 한 가지: 숫자는 '이름 값' 한 줄, 문장은 한 줄에 한 문장. 휴대폰 한 줄을 넘지 않는 길이
    - 숫자 줄은 명사형으로 짧게. '~요' 문장은 회사 소개 한 줄과 다음 소식 한 줄, 면책 한 줄뿐
    - 질문·저장 유도 문구는 카드 10장에 이미 있어 캡션에서 뺌. 구현 안 된 약속(상장일 결과 게시)도 쓰지 않음
    - 첫 두 줄(회사명 공모주 청약 / 훅 숫자)이 '더 보기' 전 미리보기와 검색에 걸리는 부분
    - 숫자는 전부 facts·derived에서 (pipeline이 카드와 같은 기준으로 대조). 해시태그는 게시물당 5개까지(2025.12~)
    """
    corp = facts.corp_name
    has_band = bool(facts.band_low_krw and facts.band_high_krw)
    if facts.demand_ratio is not None and facts.lockup_ratio_pct is not None:
        hook, used = f"기관 경쟁률 {ratio(facts.demand_ratio)}, 확약 {pct(facts.lockup_ratio_pct)}", {"demand", "lockup"}
    elif facts.demand_ratio is not None:
        hook, used = f"기관 경쟁률 {ratio(facts.demand_ratio)}", {"demand"}
    elif facts.float_ratio_pct is not None:
        hook, used = f"상장일 유통 가능 물량 {pct(facts.float_ratio_pct)}", {"float"}
    elif has_band and not facts.final_price_krw:
        hook, used = f"희망 공모가 {facts.band_low_krw:,}~{facts.band_high_krw:,}원", {"price"}
    else:
        hook, used = "", set()
    blocks: list[list[str]] = [[f"{corp} 공모주 청약", hook]]

    nums: list[str] = []
    if facts.industry:
        ind = facts.industry.strip()
        nums.append(f"{ind}{_ieyo(ind)}." if ind.endswith(("회사", "기업", "업체")) else f"{ind} 회사예요.")
    nums.append(f"청약일 {kdays(facts.subscription_start, facts.subscription_end)}" if facts.subscription_start else "")
    nums.append(broker_text(facts))
    if facts.final_price_krw:
        pos = band_pos(facts.final_price_krw, facts.band_low_krw, facts.band_high_krw)
        nums.append(f"확정 공모가 {facts.final_price_krw:,}원" + (f" ({pos})" if pos else ""))
    elif has_band and "price" not in used:
        nums.append(f"희망 공모가 {facts.band_low_krw:,}~{facts.band_high_krw:,}원")
    nums.append(deposit_text(facts))
    if facts.demand_ratio is not None and "demand" not in used:
        nums.append(f"기관 경쟁률 {ratio(facts.demand_ratio)}")
    if facts.lockup_ratio_pct is not None and "lockup" not in used:
        nums.append(f"의무보유 확약 {pct(facts.lockup_ratio_pct)}")
    if facts.float_ratio_pct is not None and "float" not in used:
        nums.append(f"상장일 유통 가능 물량 {pct(facts.float_ratio_pct)}")
    blocks.append(nums)

    if facts.demand_ratio is None:  # 수요예측 전: 언제 끝나는지 + 결과가 나오면 다시 올린다(발행조건확정 공시로 2차 게시가 실제로 돈다)
        when = [f"수요예측 {kdays(facts.forecast_start, facts.forecast_end)}"] if facts.forecast_start else []
        blocks.append(when + ["결과 나오면 경쟁률·확약으로 다시 올릴게요."])
    else:  # 수요예측 뒤: 청약 다음 일정
        blocks.append([f"환불일 {kdate(facts.refund_date)}"] * bool(facts.refund_date)
                      + [f"상장 예정일 {kdate(facts.listing_date)}"] * bool(facts.listing_date))

    blocks.append([f"출처: DART {doc} {rcept_no}", "투자 권유는 아니에요."])
    tags = ["#공모주", "#공모주청약", "#" + corp.replace(" ", ""), "#청약일정", BRAND.get("hashtag", "")]
    blocks.append([" ".join([t for t in dict.fromkeys(tags) if t and t != "#"][:5])])
    blocks = [[l for l in b if l] for b in blocks]  # 값이 없어 비는 줄은 빼서 덩어리 안에 빈 줄이 생기지 않게
    return "\n\n".join("\n".join(b) for b in blocks if b)
