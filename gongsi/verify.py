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


# 캡션 한 줄 폭. 글자 폭은 Pretendard로 잰 값(한글 = 1, 2026-10-27 측정)을 반올림했다.
# 휴대폰 캡션 한 줄은 한글 약 23~26자로 추정(화면 폭 360~393pt, 글자 14pt 가정, 확인 필요)이라 22로 여유를 둔다.
CAPTION_LINE_MAX = 22.0
_WIDTH = {" ": 0.3, "%": 1.0, "~": 0.75, "#": 0.7, "(": 0.4, ")": 0.4, "·": 0.3}


def line_width(s: str) -> float:
    w = 0.0
    for ch in s:
        if ch in _WIDTH:
            w += _WIDTH[ch]
        elif "가" <= ch <= "힣" or "㄰" <= ch <= "㆏":
            w += 1.0
        elif ch.isdigit():
            w += 0.62
        elif ch.isascii() and ch.isalpha():
            w += 0.7
        elif ch.isascii():
            w += 0.3  # . , : 같은 문장부호
        else:
            w += 1.0  # 이모지·한자 등은 넓게 잡는다
    return w


def check_caption_layout(cap: str) -> list[str]:
    """캡션 줄바꿈 규칙(2026-10-27 '줄바꿈이 제멋대로' 피드백): 빈 줄은 덩어리 사이에 하나만,
    한 줄에 한 문장, 휴대폰 한 줄 폭 안(해시태그 줄은 제외), 긴 대시 금지, 인스타 제한 2,200자."""
    problems = []
    lines = cap.split("\n")
    if not lines[0].strip() or not lines[-1].strip():
        problems.append("캡션 맨 앞이나 맨 뒤가 빈 줄")
    for i, line in enumerate(lines):
        if line != line.strip():
            problems.append(f"줄 앞뒤 공백: {line!r}")
        if i and not line.strip() and not lines[i - 1].strip():
            problems.append("빈 줄이 두 번 연속")
        if "—" in line or "–" in line:
            problems.append(f"긴 대시: \"{line}\"")
        if line.startswith("#"):
            continue
        if re.search(r"[.?!]\s+\S", line):
            problems.append(f"한 줄에 문장이 둘 이상: \"{line}\"")
        if line_width(line) > CAPTION_LINE_MAX:
            problems.append(f"휴대폰 한 줄을 넘음(폭 {line_width(line):.1f} > {CAPTION_LINE_MAX:g}): \"{line}\"")
    if len(cap) > 2200:
        problems.append(f"캡션 {len(cap)}자, 인스타 제한 2,200자 초과")
    return problems


def verify(card_texts: list[str], summary: Summary | None, facts: IpoFacts) -> list[str]:
    problems = check_numbers(card_texts, facts) + check_forbidden(card_texts)
    if summary:
        problems += check_summary(summary, facts) + check_forbidden(summary.business_lines)
    return problems
