"""LLM이 쓰는 유일한 구간: 공모주 2장의 사업 요약 2줄.

규칙
- 입력은 원문 문장 목록(business_evidence)뿐. 숫자·고유명사는 원문에 있는 것만.
- 출력 문장마다 근거 문장(evidence)을 원문에서 그대로 골라 붙인다. 검증 단계가 문자열 대조한다.
- 키가 없으면 원문 문장을 그대로 잘라 쓰는 폴백으로 간다 (요약 없음).
"""
from __future__ import annotations

import json

from gongsi.config import settings
from gongsi.models import IpoFacts, Summary

MODEL = "claude-sonnet-5-5"

PROMPT = """아래는 한 회사의 증권신고서 '사업의 내용'에서 뽑은 문장들입니다.
이 회사가 무엇을 하는 회사인지, 친구에게 설명하듯 '~해요'체로 인스타그램 카드 한 장에 들어갈 2줄로 쓰세요. 두 번째 줄은 '~해요' 또는 '~이에요'로 끝내고, 딱딱한 명사 나열(개발·제조·판매)은 피하세요.

규칙:
1. 한 줄은 공백 포함 20자 이내, 정확히 2줄. 줄 끝에 한 글자만 남는 어색한 끊김이 없게 끊으세요.
2. 아래 문장들에 없는 숫자, 회사명, 고객명, 평가 표현(호재, 유망, 기대 등)을 절대 쓰지 마세요.
3. 각 줄마다 근거가 된 원문 문장을 아래 목록에서 '그대로' 복사해 evidence에 넣으세요.
4. industry: 이 회사의 업종을 명사구 12자 이내로 (예: "AI 인프라 소프트웨어", "반도체 장비 부품", "골프 시뮬레이터"). 아래 문장에 있는 단어로만.
5. JSON만 출력: {"industry": "...", "business_lines": ["...", "..."], "evidence": ["원문 문장", "원문 문장"]}

원문 문장:
"""


RISK_PROMPT = """아래는 한 회사가 증권신고서 '투자위험요소'에 직접 쓴 항목입니다. 각 항목을 투자 초보가 읽어도 바로 이해되게 '~함/~있음/~줄었음' 같은 음슴체로 풀어 쓰세요.

규칙:
1. 항목마다 한 문단, 공백 포함 60~80자. 문장은 2개까지. 줄바꿈은 넣지 마세요(카드가 알아서 접습니다).
2. 원문에 없는 숫자, 회사명, 평가 표현(심각, 안전, 괜찮, 호재, 악재 등)은 쓰지 마세요. 원문이 "~할 수 있습니다"면 "~할 수 있음"으로, 가능성은 가능성으로 두세요.
3. 왜 위험인지(원인 → 영향)가 보이게 쓰세요. 원문에 구체적 상황(어떤 기관, 어떤 조건)이 있으면 한 가지는 살리세요.
4. JSON만 출력: {"risks": ["문단", "문단"]}  (입력 순서대로)

항목:
"""


def summarize_risks(facts: IpoFacts) -> list[list[str]]:
    """8장 위험 2개를 쉬운 말 한 문단(60~80자)씩. 키가 없거나 실패하면 빈 목록(카드는 소제목만 보여준다)."""
    risks = facts.risks[:2]
    if not settings.anthropic_api_key or not risks:
        return []
    import anthropic

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    from gongsi.verify import check_forbidden

    body = "\n\n".join(f"[{i + 1}] 소제목: {r.title}\n원문: {r.quote}" for i, r in enumerate(risks))
    extra = ""
    for _ in range(2):  # 금지어에 걸리면 그 단어를 알려주고 한 번 다시 쓰게 한다
        msg = client.messages.create(model=MODEL, max_tokens=4000, messages=[{"role": "user", "content": RISK_PROMPT + body + extra}])
        text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip()
        if "{" not in text:
            return []
        data = json.loads(text[text.find("{"): text.rfind("}") + 1])
        out = [[str(x)] if isinstance(x, str) else [str(y) for y in x][:2] for x in data.get("risks", [])][: len(risks)]
        if len(out) != len(risks):
            return []
        bad = check_forbidden([l for pair in out for l in pair])
        if not bad:
            return out
        words = sorted({p.split("'")[1] for p in bad})
        extra = f"\n\n주의: 다음 단어는 쓰지 말고 다른 표현으로 바꾸세요: {', '.join(words)}"
    return []


def fallback(facts: IpoFacts) -> Summary:
    lines = [s[:20] for s in facts.business_evidence[:2]]
    return Summary(business_lines=lines, evidence=facts.business_evidence[:2])


def summarize(facts: IpoFacts) -> tuple[Summary, str]:
    """(요약, 사용한 방식) 을 돌려줍니다."""
    if not settings.anthropic_api_key or not facts.business_evidence:
        return fallback(facts), "fallback"
    import anthropic

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    numbered = "\n".join(f"- {s}" for s in facts.business_evidence)
    msg = client.messages.create(
        model=MODEL, max_tokens=4000,
        messages=[{"role": "user", "content": PROMPT + numbered}],
    )
    # 모델이 thinking 블록을 먼저 돌려줄 수 있어 text 블록만 고른다
    text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip()
    if "{" not in text:
        raise RuntimeError(f"LLM 응답에 JSON 없음 (stop={msg.stop_reason}, blocks={[b.type for b in msg.content]}): {text[:200]}")
    text = text[text.find("{"): text.rfind("}") + 1]
    data = json.loads(text)
    industry = str(data.pop("industry", "") or "").strip()
    if industry and len(industry) <= 16:
        facts.industry = industry  # 원문 정규식(당사가 영위하는 … 사업)보다 LLM 명사구가 안정적. 숫자 없는 라벨이라 검증은 금지어만
    return Summary(**data, risk_lines=summarize_risks(facts)), MODEL
