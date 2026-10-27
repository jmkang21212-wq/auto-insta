"""규칙 검증. LLM이 아니라 코드가 판정하고, 실패 사유를 문자열로 돌려줍니다.

세 가지를 봅니다.
1. 숫자 대조: 카드 문구에 등장하는 모든 숫자가 IpoFacts.number_set()에 있어야 한다.
2. 근거 대조: LLM 요약의 evidence 문장이 원문(business_evidence)에 실제로 있어야 하고,
   요약 줄에 든 숫자는 그 근거 문장 안에도 있어야 한다.
3. 금지어: 투자 판단으로 읽히는 표현이 있으면 실패.
"""
from __future__ import annotations

import re

from gongsi.models import IpoFacts, Summary

FORBIDDEN = [
    "호재", "악재", "추천", "기회", "유망", "매수", "매도", "급등", "급락", "대박",
    "꼭", "반드시", "놓치지", "강추", "수익률", "고수익", "수익 보장", "수익이 기대", "먹튀", "따상", "따블", "기대됩니다", "전망",
    # '수익' 단독은 '영업수익·수익성'(사실 서술)까지 막아서 뺐다 (2026-10-27)
]

# 카드 번호(1/8), 접수번호, 퍼센트·금액 안의 숫자를 뽑는다. 소수점·콤마 포함
NUM_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")

# 카드 자체가 쓰는 숫자 (장 번호, D-day 등)는 대조에서 뺀다
ALWAYS_OK = {str(i) for i in range(0, 32)} | {"2026", "2027", "1080", "1350", "8"}


def numbers_in(text: str) -> set[str]:
    return set(NUM_RE.findall(text))


def check_numbers(texts: list[str], facts: IpoFacts) -> list[str]:
    allowed = facts.number_set() | ALWAYS_OK
    problems = []
    for t in texts:
        for n in numbers_in(t):
            if n in allowed or n.replace(",", "") in allowed:
                continue
            problems.append(f"숫자 '{n}' 이 필드값에 없음: \"{t[:40]}\"")
    return problems


def check_summary(summary: Summary, facts: IpoFacts) -> list[str]:
    problems = []
    src = facts.business_evidence
    if len(summary.business_lines) != len(summary.evidence):
        problems.append("요약 줄 수와 근거 수가 다름")
    for line, ev in zip(summary.business_lines, summary.evidence):
        if ev not in src:
            problems.append(f"근거 문장이 원문에 없음: \"{ev[:40]}\"")
            continue
        for n in numbers_in(line):
            if n not in ev and n.replace(",", "") not in ev.replace(",", ""):
                problems.append(f"요약의 숫자 '{n}' 이 근거 문장에 없음")
        if len(line) > 20:
            problems.append(f"요약 줄이 20자 초과: {len(line)}자")
    return problems


def check_risks(summary: Summary, facts: IpoFacts) -> list[str]:
    """8장 위험 요약: 항목당 90자 이내, 숫자는 그 위험의 원문 안에 있어야 함."""
    problems = []
    for pair, risk in zip(summary.risk_lines, facts.risks):
        if sum(len(l) for l in pair) > 90:
            problems.append(f"위험 요약이 90자 초과: {sum(len(l) for l in pair)}자")
        for line in pair:
            for n in numbers_in(line):
                if n not in risk.quote and n.replace(",", "") not in risk.quote.replace(",", ""):
                    problems.append(f"위험 요약의 숫자 '{n}' 이 원문에 없음")
    return problems


def check_forbidden(texts: list[str]) -> list[str]:
    return [f"금지어 '{w}': \"{t[:40]}\"" for t in texts for w in FORBIDDEN if w in t]


def verify(card_texts: list[str], summary: Summary | None, facts: IpoFacts) -> list[str]:
    problems = check_numbers(card_texts, facts) + check_forbidden(card_texts)
    if summary:
        problems += check_summary(summary, facts) + check_forbidden(summary.business_lines)
    return problems
