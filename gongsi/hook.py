"""훅: 릴스 첫 화면과 캡션 첫 줄에 쓰는 한 문장 (2026-10-27, ekke.now 참고).

정보 나열이 아니라 1초 안에 이해되고 궁금해지는 한 줄. 회사 주력 제품을 누구나 아는 물건에 빗댄다.
  예) 반도체 장비 속 '샤워기' 만드는 회사가 상장함   ← 신고서 용어 해설: 샤워헤드 = 공정 가스를 균일하게 분사하는 부품
둘째 줄은 숫자 하나 + 이모지 (수요예측 뒤 경쟁률, 전엔 최소 증거금).

LLM이 후보 3개를 쓰고, 코드가 근거를 대조한다:
- 빗댄 제품 용어(term)가 신고서 '1. 사업의 개요' 문장이나 '전문용어 해설' 표에 실제로 있어야 함
- 제목·자막에 숫자 금지(숫자는 둘째 줄에서 facts로만), 금지어 금지, 휴대폰 한 줄 폭 안
- 캡션의 ':' 풀이 줄은 신고서 용어 해설 문장을 그대로 쓴다 (LLM이 풀어 쓰지 않음)
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from gongsi.config import settings
from gongsi.models import IpoFacts
from gongsi.verify import CAPTION_LINE_MAX, check_forbidden, line_width

MODEL = "claude-sonnet-5-5"
HOT_RATIO = 500  # 경쟁률 이모지: 이 이상이면 🔥, 아래면 👀 (근거: 비교 기준 LG CNS 114:1, HD현대마린솔루션 201:1, 더본코리아 734.67:1 사이에서 정함)

PROMPT = """너는 인스타 경제 계정 ekke.now 같은 말투로 공모주 릴스 첫 화면 제목을 쓰는 카피라이터야.

좋은 예: "반도체 장비 속 '샤워기' 만드는 회사가 상장함"
  (신고서 용어 해설에 '샤워헤드 = 반도체 장비 내 공정 가스를 균일하게 분사하는 핵심 부품'이 있어서, 누구나 아는 샤워기에 빗댐)
나쁜 예: "고온·고압·플라즈마 출근" (무슨 말인지 1초 안에 모름), "품질 승인이라는 이름의 면접" (회사 내부 얘기라 공감 안 됨)

규칙
1. 1초 안에 이해되는 한 문장. "~ 만드는 회사가 상장함" 또는 "~ 하는 회사가 상장함" 꼴. 22자 이내.
2. 회사 주력 제품·서비스를 누구나 아는 일상 물건이나 장면에 빗대 작은따옴표로 감싼다. 빗댈 게 마땅치 않으면 쉬운 말로 그대로.
3. 빗댄 근거 제품 용어를 아래 '사업 원문'이나 '용어 해설'에서 철자 그대로 골라 term에 적는다.
4. 숫자 쓰지 마 (숫자는 둘째 줄에 따로 붙는다). 투자 권유·평가 표현 금지 (대박, 따상, 유망, 좋다, 위험하다, 수익 등).
5. sub: 영상 위에 얹을 밈 같은 한마디. 빗댄 걸 살짝 비트는 말 (예: "진짜 샤워기 아님"). 12자 이내, 숫자 금지.
6. emoji: 제목 앞에 붙일 이모지 1개 (빗댄 물건).
7. video: 그 일상 물건이 크게 나오는 스톡 영상 검색어. 영어 2~4단어, 사람 얼굴과 브랜드 로고가 안 나올 만한 것.
8. 후보 3개, 서로 다른 제품이나 각도로. JSON만: {"hooks":[{"title":"","term":"","emoji":"","sub":"","video":""}]}

회사: {corp} / 업종: {industry}
사업 원문:
{evidence}
용어 해설(신고서 표):
{glossary}
"""


@dataclass
class Hook:
    title: str  # 첫 줄 (숫자 없음)
    term: str  # 빗댄 근거 제품 용어 (신고서 원문 그대로)
    emoji: str
    sub: str  # 영상 위 자막
    video: str  # 스톡 영상 검색어 (영어)
    explain: str = ""  # 캡션 ':' 줄. 신고서 용어 해설 그대로

    def headline(self) -> str:
        return f"{self.emoji} {self.title}".strip()


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().lower())


def _names(key: str) -> set[str]:
    """'Showerhead(샤워헤드)' → {'showerhead(샤워헤드)', 'showerhead', '샤워헤드'}"""
    out = {_norm(key)}
    m = re.match(r"^(.*?)\s*\((.*)\)\s*$", key)
    if m:
        out |= {_norm(m.group(1)), _norm(m.group(2))}
    return {n for n in out if n}


def aliases(evidence: list[str]) -> dict[str, set[str]]:
    """사업 원문의 '스핀척(Spin Chuck)' 같은 한글(영문) 짝. 용어 해설 표 키가 영어뿐일 때 한글 용어로 찾으려고"""
    al: dict[str, set[str]] = {}
    for ko, en in re.findall(r"([가-힣][가-힣A-Za-z]*)\s*\(([A-Za-z][A-Za-z0-9 \-/]*?)\)", " ".join(evidence)):
        al.setdefault(_norm(ko), set()).add(en.strip())
        al.setdefault(_norm(en), set()).add(ko.strip())
    return al


def _gloss_key(term: str, glossary: dict[str, str], al: dict[str, set[str]] | None = None) -> str | None:
    """'샤워헤드' → 'Showerhead(샤워헤드)', '스핀척' → 'Spin Chuck'(원문 짝으로) 처럼 용어 해설 표의 키 찾기.
    이름이 똑같은 키를 먼저, 없으면 부분 일치(2글자 이상)."""
    cands = [term] + sorted((al or {}).get(_norm(term), set()))
    for c in cands:
        for k in glossary:
            if _norm(c) in _names(k):
                return k
    for c in cands:
        t = _norm(c)
        for k in glossary:
            if len(t) >= 2 and any(len(n) >= 2 and (t in n or n in t) for n in _names(k)):
                return k
    return None


def display_term(key: str) -> str:
    """'Showerhead(샤워헤드)' → '샤워헤드' (괄호 안 한글이 있으면 그걸 쓴다)"""
    m = re.search(r"\(([^)]*[가-힣][^)]*)\)", key)
    return (m.group(1) if m else key).strip()


def _josa(word: str, a: str, b: str) -> str:
    ch = word[-1]
    return a if "가" <= ch <= "힣" and (ord(ch) - 0xAC00) % 28 else b


def metaphor(title: str) -> str:
    """제목의 작은따옴표 안 비유: "반도체 장비 속 '샤워기' 만드는…" → '샤워기'"""
    m = re.search(r"['‘]([^'’]+)['’]", title)
    return m.group(1).strip() if m else ""


def explain_line(term: str, glossary: dict[str, str], al: dict[str, set[str]] | None = None, title: str = "") -> str:
    """캡션 ':' 풀이 줄: 신고서 용어 해설 문장을 그대로 붙인다 (숫자가 든 풀이는 숫자 대조 때문에 쓰지 않음).
    해설 표에 없으면 비유가 무엇을 빗댄 건지만 밝힌다 (용어는 원문에 있다는 걸 check_hook이 확인함)."""
    korean = term if re.search(r"[가-힣]", term) else next((a for a in sorted((al or {}).get(_norm(term), set()))
                                                            if re.search(r"[가-힣]", a)), "")
    key = _gloss_key(term, glossary, al)
    if key and not re.search(r"\d", glossary[key]):
        name = korean or display_term(key)
        d = glossary[key].strip()
        d = re.split(r"(?<=[.?!])\s+", d)[0].rstrip(".")  # 해설이 두 문장이면 첫 문장(정의)만
        return f": {name}{_josa(name, '은', '는')} {d}" + ("" if d.endswith(("임", "함", "음", "다")) else "임")
    m = metaphor(title)
    if m and term:
        name = korean or term
        return f": '{m}'{_josa(m, '은', '는')} 이 회사 제품 {name}{_josa(name, '을', '를')} 빗댄 말임"
    return ""


def check_hook(h: Hook, facts: IpoFacts) -> list[str]:
    """근거·말투 검사. 문제가 없으면 빈 목록"""
    problems = []
    source = " ".join(facts.business_evidence) + " " + " ".join(f"{k} {v}" for k, v in facts.glossary.items())
    if not h.term or (h.term.lower() not in source.lower() and not _gloss_key(h.term, facts.glossary)):
        problems.append(f"근거 용어 '{h.term}'가 신고서 사업 원문·용어 해설에 없음")
    for name, text, limit in (("제목", h.title, CAPTION_LINE_MAX - 1.3), ("자막", h.sub, 12.0)):
        if re.search(r"\d", text):
            problems.append(f"{name}에 숫자: {text}")
        if "—" in text or "–" in text:
            problems.append(f"{name}에 긴 대시: {text}")
        if line_width(text) > limit:
            problems.append(f"{name}이 너무 김({line_width(text):.1f}): {text}")
    problems += check_forbidden([h.title, h.sub])
    if not re.fullmatch(r"[A-Za-z][A-Za-z \-]{2,40}", h.video or ""):
        problems.append(f"영상 검색어가 영어 2~4단어가 아님: {h.video}")
    return problems


def make_hooks(facts: IpoFacts) -> list[Hook]:
    """후보 3개 중 검사를 통과한 것만 돌려준다. 키가 없거나 근거가 없으면 빈 목록 (그땐 기존 제목을 쓴다)"""
    if not settings.anthropic_api_key or not (facts.business_evidence or facts.glossary):
        return []
    import anthropic

    gl = "\n".join(f"- {k}: {v}" for k, v in list(facts.glossary.items())[:60])
    ev = "\n".join(f"- {s}" for s in facts.business_evidence[:20])
    prompt = (PROMPT.replace("{corp}", facts.corp_name).replace("{industry}", facts.industry or "")
              .replace("{evidence}", ev).replace("{glossary}", gl or "(없음)"))
    msg = anthropic.Anthropic(api_key=settings.anthropic_api_key).messages.create(
        model=MODEL, max_tokens=4000, messages=[{"role": "user", "content": prompt}])
    text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
    if "{" not in text:
        return []
    data = json.loads(text[text.find("{"): text.rfind("}") + 1])
    hooks = []
    al = aliases(facts.business_evidence)
    for raw in data.get("hooks", [])[:3]:
        h = Hook(**{k: str(raw.get(k, "")).strip() for k in ("title", "term", "emoji", "sub", "video")})
        if not check_hook(h, facts):
            h.explain = explain_line(h.term, facts.glossary, al, h.title)
            hooks.append(h)
    # 캡션에서 신고서 용어 해설로 풀어 줄 수 있는 훅을 앞으로 (빗댄 말만 밝히는 훅은 뒤로). 같은 급이면 모델이 쓴 순서
    hooks.sort(key=lambda h: 0 if h.explain and "빗댄 말임" not in h.explain else 1 if h.explain else 2)
    return hooks


def line2(facts: IpoFacts) -> str:
    """둘째 줄: 숫자 하나 + 이모지. 수요예측 뒤엔 경쟁률, 전엔 최소 증거금(밴드 하단 기준), 그것도 없으면 청약일"""
    from gongsi.cards import man, ratio

    if facts.demand_ratio is not None:
        return f"경쟁률 {ratio(facts.demand_ratio)} {'🔥' if facts.demand_ratio >= HOT_RATIO else '👀'}"
    if facts.min_deposit_krw:
        return f"{man(facts.min_deposit_krw)}원이면 청약 가능 💸"
    if facts.min_shares and facts.deposit_rate_pct and facts.band_low_krw:
        return f"{man(facts.min_shares * facts.band_low_krw * facts.deposit_rate_pct // 100)}원부터 청약 가능 💸"
    if facts.subscription_start:
        m, d = int(facts.subscription_start[5:7]), int(facts.subscription_start[8:10])
        return f"{m}월 {d}일 청약 시작 ⏰"
    return ""
