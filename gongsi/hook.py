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
from dataclasses import dataclass, field

from gongsi.config import settings
from gongsi.models import IpoFacts
from gongsi.verify import CAPTION_LINE_MAX, check_forbidden, line_width

MODEL = "claude-sonnet-5-5"
TERMS = {k: v for k, v in json.loads((settings.fixtures_dir / "terms.json").read_text(encoding="utf-8")).items() if not k.startswith("_")}
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
7. video: 그 비유가 '움직이는 장면'으로 나오는 스톡 영상 검색어. 영어 2~4단어. 사람이나 동물이 뭔가 하는 재밌는 장면이면 더 좋음
   (예: 샤워기 → 'dog shower bath', 균형 감각 → 'tightrope walker balancing'). 가만히 있는 풍경·제품 사진 같은 장면 금지, 브랜드 로고가 안 나올 만한 것.
8. videos: 7번 검색어 3개를 구체적인 장면 → 더 흔한 장면 순서로 (무료 스톡 영상 사이트에 있을 만한 것. 셋째는 아주 흔한 장면,
   예: ["kid finger painting", "colorful paint splash", "ink in water"]). video에는 그중 첫째를 적는다.
9. 후보 3개, 서로 다른 제품이나 각도로.
10. use: 이 회사 제품이 실제로 어디에 쓰이는지 한 줄 (썸네일에 들어감). 예: "반도체 장비에 들어감", "AI 데이터센터·수소차에 쓰임".
   사업 원문에 나온 용도·고객 산업 낱말만, 18자 이내, 숫자·과장 금지. 회사마다 하나.
JSON만: {"use":"","hooks":[{"title":"","term":"","emoji":"","sub":"","video":"","videos":["","",""]}]}

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
    alt_videos: list[str] = field(default_factory=list)  # video로 못 찾으면 차례로 (구체적 → 흔한 장면)
    use: str = ""  # 썸네일 '📍 어디에 쓰이는지' 한 줄 (회사마다 같음, 사업 원문 낱말만)

    def queries(self) -> list[str]:
        return [q for q in dict.fromkeys([self.video, *self.alt_videos]) if q]

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
    own = sorted(_names(term)) if term.strip() else []  # '샤워헤드(Showerhead)'면 통째, 샤워헤드, showerhead
    cands = own + sorted({a for n in own for a in (al or {}).get(n, set())})
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


def _korean(s: str) -> str:
    """'샤워헤드(Showerhead)'·'Showerhead(샤워헤드)' → '샤워헤드', '스핀척' → '스핀척', 'Spin Chuck' → '' (캡션엔 한글 이름만)"""
    m = re.match(r"^(.*?)\s*\((.*)\)\s*$", s.strip())
    return next((p.strip() for p in ([m.group(1), m.group(2)] if m else [s]) if re.search(r"[가-힣]", p)), "")


def _josa(word: str, a: str, b: str) -> str:
    ch = word[-1]
    return a if "가" <= ch <= "힣" and (ord(ch) - 0xAC00) % 28 else b


def metaphor(title: str) -> str:
    """제목의 작은따옴표 안 비유: "반도체 장비 속 '샤워기' 만드는…" → '샤워기'"""
    m = re.search(r"['‘]([^'’]+)['’]", title)
    return m.group(1).strip() if m else ""


_ENDINGS = (("입니다", "임"), ("이다", "임"), ("있습니다", "있음"), ("없습니다", "없음"), ("됩니다", "됨"), ("합니다", "함"))


def _eumseum(s: str) -> str:
    """신고서 문장 끝을 음슴체로: '…구성 요소입니다.' → '…구성 요소임'. 모르는 끝이면 빈 문자열"""
    s = s.strip().rstrip(".")
    for a, b in _ENDINGS:
        if s.endswith(a):
            return s[: -len(a)] + b
    return s if s.endswith(("임", "함", "음", "됨")) else ""


def definition_from_evidence(names: list[str], evidence: list[str]) -> str:
    """사업 원문에서 '자이로스코프(Gyroscope)는 …입니다.' 같은 정의 문장을 찾아 음슴체로 (용어 해설 표가 없는 회사용).
    영문 괄호와 따옴표만 빼고 원문 그대로. 숫자가 들었거나 너무 길면 안 씀 (숫자 대조·읽기)"""
    for s in evidence:
        t = re.sub(r"[“”\"]", "", s).strip()
        for n in names:
            if n and re.match(rf"^{re.escape(n)}\s*(\([^)]*\))?\s*(은|는)\s", t, flags=re.I):
                t = re.sub(r"\s*\([^)]*[A-Za-z][^)]*\)", "", t)
                d = _eumseum(re.split(r"(?<=[.?!])\s+", t)[0])
                if d and not re.search(r"\d", d) and len(d) <= 90:
                    return d
    return ""


def _alike(a: str, b: str) -> bool:
    """비유와 용어가 두 글자 이상 겹치면(샤워기·샤워헤드) '빗댄 말' 줄 없이도 통한다"""
    a, b = a.replace(" ", ""), b.replace(" ", "")
    return any(a[i:i + 2] in b for i in range(len(a) - 1))


def explain_parts(term: str, glossary: dict[str, str], al: dict[str, set[str]] | None = None, title: str = "",
                  evidence: list[str] | None = None) -> tuple[str, str]:
    """(비유가 무엇을 빗댔는지, 그 용어의 정의). 정의는 신고서 용어 해설 표 → 없으면 사업 원문의 정의 문장."""
    korean = _korean(term) or next((k for a in sorted((al or {}).get(_norm(term), set())) if (k := _korean(a))), "")
    key = _gloss_key(term, glossary, al)
    name = korean or (display_term(key) if key else term.strip())
    definition = ""
    if key and not re.search(r"\d", glossary[key]):
        d = glossary[key].strip()
        d = re.split(r"(?<=[.?!])\s+", d)[0].rstrip(".")  # 해설이 두 문장이면 첫 문장(정의)만
        definition = f"{name}{_josa(name, '은', '는')} {d}" + ("" if d.endswith(("임", "함", "음", "다")) else "임")
    elif evidence:
        names = [n for n in dict.fromkeys([name, term.strip(), *sorted((al or {}).get(_norm(term), set()))]) if n]
        definition = definition_from_evidence(names, evidence)
    if not definition:  # 신고서에 정의가 없으면 우리가 쓴 고정 풀이(fixtures/terms.json)
        fixed = next((TERMS[n] for n in (name, term.strip()) if n in TERMS), "")
        if fixed:
            definition = f"{name}{_josa(name, '은', '는')} {fixed}" + ("" if fixed.endswith(("임", "함", "음", "다")) else "임")
    m = metaphor(title)
    mapping = f"'{m}'{_josa(m, '은', '는')} {name}{_josa(name, '을', '를')} 빗댄 말임" if m and name and not _alike(m, name) else ""
    return mapping, definition


def explain_line(term: str, glossary: dict[str, str], al: dict[str, set[str]] | None = None, title: str = "",
                 evidence: list[str] | None = None) -> str:
    """캡션 ':' 풀이: 비유가 덜 뻔하면 무엇을 빗댔는지 + 그 용어 정의(신고서 문장 그대로). 문장마다 ':' 줄로 나뉜다.
    정의를 못 찾으면 빗댄 대상만 밝힌다 (용어가 원문에 있다는 건 check_hook이 확인함)."""
    parts = [p for p in explain_parts(term, glossary, al, title, evidence) if p]
    return (": " + ". ".join(parts)) if parts else ""


_USE_END = re.compile(r"\s*(?:에서|에|으로|로)?\s*(?:쓰임|들어감|사용됨|씀|공급됨|납품됨)$")


def check_use(use: str, facts: IpoFacts) -> list[str]:
    """썸네일 '쓰이는 곳' 줄 검사: 낱말마다 신고서 사업 원문·용어 해설에 있어야 함 (띄어쓰기 무시), 숫자·금지어 없음, 한 줄 폭 안"""
    if not use:
        return ["쓰이는 곳이 비어 있음"]
    src = re.sub(r"\s", "", " ".join(facts.business_evidence) + " " + " ".join(f"{k} {v}" for k, v in facts.glossary.items()))
    words = [w for w in re.split(r"[\s·,/]+", _USE_END.sub("", use)) if w]
    problems = [f"쓰이는 곳 낱말 '{w}'가 사업 원문에 없음" for w in words if re.sub(r"\s", "", w) not in src]
    if re.search(r"\d", use):
        problems.append(f"쓰이는 곳에 숫자: {use}")
    if line_width(use) > 20:
        problems.append(f"쓰이는 곳이 너무 김({line_width(use):.1f}): {use}")
    return problems + check_forbidden([use])


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
    problems += check_forbidden([h.title, h.sub, h.emoji])  # 이모지도 (🚀📈 같은 상승 암시)
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
    hooks, rank = [], {}
    al = aliases(facts.business_evidence)
    use = str(data.get("use", "")).strip()
    if check_use(use, facts):
        use = ""  # 원문에 없는 낱말이 있으면 쓰이는 곳 줄은 빼고 간다
    for raw in data.get("hooks", [])[:3]:
        h = Hook(**{k: str(raw.get(k, "")).strip() for k in ("title", "term", "emoji", "sub", "video")})
        alts = [str(q).strip() for q in (raw.get("videos") or []) if isinstance(q, str)]
        h.alt_videos = [q for q in alts if q != h.video and re.fullmatch(r"[A-Za-z][A-Za-z \-]{2,40}", q)][:3]
        h.use = use
        if not check_hook(h, facts):
            h.explain = explain_line(h.term, facts.glossary, al, h.title, facts.business_evidence)
            rank[id(h)] = 0 if explain_parts(h.term, facts.glossary, al, h.title, facts.business_evidence)[1] else 1 if h.explain else 2
            hooks.append(h)
    # 캡션에서 신고서 문장으로 용어 정의까지 풀어 줄 수 있는 훅을 앞으로 (빗댄 대상만 밝히는 훅은 뒤로). 같은 급이면 모델이 쓴 순서
    hooks.sort(key=lambda h: rank[id(h)])
    return hooks


def previous_hook(facts: IpoFacts) -> Hook | None:
    """같은 회사의 수요예측 전 게시물(posts/)에서 고른 훅. 경쟁률 릴스를 같은 비유로 이어 가려고
    (사용자가 고른 형식: "반도체 장비 속 '샤워기' 만드는 회사가 상장함 / 경쟁률 ~ 🔥"). 근거 대조를 다시 통과해야 씀"""
    import json

    from gongsi.publish.store import all_posts

    for post in reversed(all_posts()):
        f = post.dir / "bundle.json"
        # 앞서 올린 같은 회사 게시물 (수요예측 전·후·상장 전날) 가운데 가장 최근 것의 훅 (2026-10-28 여러 번 다루기)
        if post.meta.get("corp") != facts.corp_name or post.meta.get("phase") not in ("pre", "post", "listing") or not f.exists():
            continue
        if post.meta.get("status") == "rejected":
            continue
        b = json.loads(f.read_text(encoding="utf-8"))
        if not b.get("hooks"):
            continue
        raw = b["hooks"][min(int(b.get("chosen") or 0), len(b["hooks"]) - 1)]
        h = Hook(**{k: str(raw.get(k, "")) for k in ("title", "term", "emoji", "sub", "video", "use")},
                 alt_videos=[str(v) for v in raw.get("alt_videos") or []])
        h.title = phase_title(h.title, "pre")  # 단계 꼬리('내일 상장함' 등)는 떼고 원래 제목으로
        if check_hook(h, facts):
            return None
        h.explain = explain_line(h.term, facts.glossary, aliases(facts.business_evidence), h.title, facts.business_evidence)
        return h
    return None


_TITLE_TAIL = re.compile(r"(?:\s*(?:가|이)\s*(?:내일|곧|[월화수목금토일]요일)?\s*상장함|,\s*묶인 주식 풀림)$")


def phase_title(title: str, phase: str, listing_date: str | None = None, today=None) -> str:
    """훅 제목 꼬리를 게시 단계에 맞게 (2026-10-28 공모주 여러 번 다루기):
    pre/post '…회사가 상장함' / listing '…회사가 내일 상장함'(주말 끼면 '월요일 상장함') / lockup* '…회사, 묶인 주식 풀림'.
    꼬리 꼴을 모르는 제목은 그대로 둔다. 여러 번 바꿔도 같은 결과"""
    from datetime import date as _date

    base = _TITLE_TAIL.sub("", title).rstrip()
    if base == title.rstrip() and not title.rstrip().endswith("상장함"):
        return title
    if phase.startswith("lockup"):
        return f"{base}, 묶인 주식 풀림"
    josa = _josa(base, "이", "가")
    if phase == "listing" and listing_date and today:
        ld = _date.fromisoformat(listing_date)
        when = "내일" if (ld - today).days == 1 else f"{'월화수목금토일'[ld.weekday()]}요일"
        return f"{base}{josa} {when} 상장함"
    return f"{base}{josa} 상장함"


def line2(facts: IpoFacts) -> str:
    """영상 아래 줄: 공모가 → 주관사가 매긴 몸값 (신고서에 평가가액이 있을 때). 없으면 경쟁률.
    경쟁률 (2026-10-28 사용자 피드백: '얼마부터 청약 가능'보다 경쟁률이 더 궁금함).
    수요예측 뒤엔 기관 경쟁률 + 🔥(500:1 이상)/👀, 전엔 경쟁률이 언제 나오는지(신고서의 수요예측 마감일).
    청약 전에 나오는 건 기관 수요예측 경쟁률이라 '기관'을 붙인다 (일반 청약 경쟁률은 청약 당일 증권사가 발표)"""
    from gongsi.cards import ratio

    # 2026-10-28 사용자 결정: 경쟁률 대신 '공모가 → 주관사가 매긴 몸값'을 원 단위로 (경쟁률은 캡션에 그대로).
    # 같은 날 밤: 화면엔 가격만 '15,300원 → 21,304원', 두 숫자가 뭔지와 '적정주가 아님'은 캡션 첫머리에
    if facts.valuation_krw and facts.final_price_krw:
        return f"{facts.final_price_krw:,}원 → {facts.valuation_krw:,}원"
    if facts.valuation_krw and facts.band_low_krw and facts.band_high_krw:  # 수요예측 전: 희망 공모가 범위
        return f"{facts.band_low_krw:,}~{facts.band_high_krw:,}원 → {facts.valuation_krw:,}원"
    if facts.demand_ratio is not None:
        return f"기관 경쟁률 {ratio(facts.demand_ratio)} {'🔥' if facts.demand_ratio >= HOT_RATIO else '👀'}"
    if facts.forecast_end:
        m, d = int(facts.forecast_end[5:7]), int(facts.forecast_end[8:10])
        return f"경쟁률은 {m}월 {d}일 이후 공개 👀"
    return "경쟁률은 청약 전에 공개 👀"
