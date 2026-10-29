"""증권신고서·투자설명서 원문(document.xml)에서 구조화 API에 없는 항목을 뽑습니다.

원문은 DART 고유 XML(dart4.xsd)이고 본문이 대부분 표(TABLE/TR/TD)입니다. 태그를 걷어내되 표의 행은 한 줄,
셀은 ' | '로 남겨서 "행 머리 → 값" 규칙으로 읽습니다. 2026-10 실제 공시(래블업 20261021000341)로 확인한 위치:

  공모가 밴드        "희망공모가액 19,000원 ~ 22,000원" 문장
  확정 공모가        "확정공모가액을 19,000원으로" 문장
  수요예측 경쟁률    '(가) 수요예측 참여내역' 표의 "| 경쟁률 |" 행 마지막 셀(합계)
  의무보유 확약 비율 '(다) 의무보유 확약기간별 수요예측 참여내역' 합계표: 확약 행 수량 합 / 합계 수량
  유통 가능 물량     '[상장 후 시점별 유통가능주식 현황]' 표의 "상장일 유통가능" 행 비율
  최대주주 지분      '[상장 후 유통가능 물량 세부내역]' 표의 "최대주주(본인)" 행, 공모 후 지분율
  청약·납입·환불     공모개요 표의 "청약기일 | 납입기일" 행과 "...에 환불합니다" 문장
  상장 예정일        "상장 예정일 YYYY년 M월 D일" 문장 (없으면 None — 보통 "아직 확정되지 않았으며")
  주관사             공모개요 표의 대표주관회사/공동주관회사/인수회사 행
  최소 청약          '청약주식별 청약단위' 표 첫 행과 "청약증거금은 청약금액의 50%"
  실적               'III. 재무에 관한 사항 > 1. 요약재무정보' 표의 매출액(영업수익)·영업이익, 최근 결산연도 열
  자금 사용 목적     'V. 자금의 사용목적' 의 "시설자금 | 운영자금 | ... | 계" 표 (단위 백만원)
  사업 문장          '1. 사업의 개요' 본문 문장들 (LLM 요약의 근거 대조용)
  위험               요약정보 '핵심투자위험' 표의 사업위험/회사위험 첫 항목 (원문 그대로)

여기서 나온 값은 전부 공시 원문 그대로이고, 계산한 값(확약 비율, 최소 증거금)은 derived에 공식을 남깁니다.
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass, field

KDATE = r"(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일"
DOTDATE = r"(\d{4})\s*\.\s*(\d{1,2})\s*\.\s*(\d{1,2})"  # 2026.10.22


# --- XML → 줄 단위 텍스트 -------------------------------------------------------
def to_text(xml: str) -> str:
    x = re.sub(r"\s+", " ", xml)  # 원문의 줄바꿈은 의미 없음
    x = re.sub(r"<BR\s*/?>", " ", x, flags=re.I)  # 셀 안 줄바꿈은 공백으로 (행 구조를 깨지 않게)
    # 셀 안의 P·BR·SPAN은 전부 공백으로: 셀 내용이 여러 줄이어도 표의 한 행은 한 줄이어야 한다
    x = re.sub(r"<(TD|TH)\b[^>]*>(.*?)</\1>", lambda m: " | " + re.sub(r"<[^>]+>", " ", m.group(2)), x, flags=re.S)
    x = re.sub(r"<(TD|TH)\b[^>]*>", " | ", x)
    x = re.sub(r"</TR>", "\n", x)
    x = re.sub(r"</(P|TITLE|TABLE)>", "\n", x)
    x = re.sub(r"<[^>]+>", "", x)
    x = html.unescape(x)
    x = re.sub(r"[ \t\xa0]+", " ", x)
    x = re.sub(r" *\n *", "\n", x)
    return re.sub(r"\n{2,}", "\n", x).strip()


def body_text(text: str) -> str:
    """투자설명서·정정신고서는 앞부분이 '정정표(정정 전/후)'라 숫자가 두 번 나옵니다. '요약정보' 제목부터가 본문입니다."""
    lines = text.split("\n")
    for i, ln in enumerate(lines):
        if ln.strip() == "요약정보":
            return "\n".join(lines[i:])
    return text


# --- 셀 도우미 ------------------------------------------------------------------
def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _int(s: str | None) -> int | None:
    if s is None:
        return None
    s = s.strip()
    neg = s.startswith("(") and s.endswith(")")
    digits = re.sub(r"[^\d]", "", s)
    if not digits:
        return None
    v = int(digits)
    return -v if neg else v


def _float(s: str | None) -> float | None:
    if s is None:
        return None
    m = re.search(r"-?[\d,]*\.?\d+", s)
    return float(m.group(0).replace(",", "")) if m else None


def _kdate(s: str | None) -> str | None:
    if not s:
        return None
    m = re.search(KDATE, s) or re.search(DOTDATE, s)
    return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}" if m else None


def _krange(s: str) -> tuple[str | None, str | None]:
    parts = s.split("~")
    a = _kdate(parts[0])
    if len(parts) < 2:
        return a, a
    b = _kdate(parts[1])
    if b is None and a:  # "10월 19일(월)" / "10.23(금)" 처럼 연도 생략
        m = re.search(r"(\d{1,2})\s*월\s*(\d{1,2})\s*일", parts[1]) or re.search(r"(\d{1,2})\s*\.\s*(\d{1,2})", parts[1])
        if m:
            b = f"{a[:4]}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
    return a, b


def _full_year(h: str) -> bool:
    """기간 헤더 셀이 '결산 연도(12개월)'인지. 반기·분기 열은 제외. 3월 결산처럼 '2025.04.01~2026.03.31'도 인정."""
    if re.search(r"반기|분기", h):
        return False
    ds = re.findall(r"(\d{4})\s*\.\s*(\d{1,2})\s*\.\s*(\d{1,2})", h)
    if len(ds) >= 2:
        from datetime import date as _d
        try:
            a, b = (_d(int(y), int(m), int(d)) for y, m, d in ds[:2])
            return (b - a).days >= 300
        except ValueError:
            return False
    if re.search(r"12\s*[.월]", h):
        return True
    return bool(re.search(r"(19|20)\d{2}", h)) and not re.search(r"\d{1,2}\s*[.월]\s*(말|\d)", h) and "~" not in h


def _find(lines: list[str], pred, start: int = 0, limit: int | None = None) -> int:
    end = len(lines) if limit is None else min(len(lines), start + limit)
    for i in range(start, end):
        if pred(lines[i]):
            return i
    return -1


# --- 결과 -----------------------------------------------------------------------
@dataclass
class DocFacts:
    market: str | None = None
    industry: str | None = None
    band_low_krw: int | None = None
    band_high_krw: int | None = None
    final_price_krw: int | None = None
    shares_offered: int | None = None
    offering_amount_krw: int | None = None
    demand_ratio: float | None = None
    lockup_ratio_pct: float | None = None
    float_ratio_pct: float | None = None
    major_holder_after_pct: float | None = None
    forecast_start: str | None = None
    forecast_end: str | None = None
    final_price_date: str | None = None
    subscription_start: str | None = None
    subscription_end: str | None = None
    payment_date: str | None = None
    refund_date: str | None = None
    listing_date: str | None = None
    underwriters: list[str] = field(default_factory=list)
    min_shares: int | None = None
    deposit_rate_pct: int | None = None
    min_deposit_krw: int | None = None
    fiscal_year: int | None = None
    revenue_krw: int | None = None
    operating_income_krw: int | None = None
    use_of_proceeds: list[tuple[str, int]] = field(default_factory=list)
    risks: list[tuple[str, str, str]] = field(default_factory=list)  # (category, title, body)
    business_evidence: list[str] = field(default_factory=list)
    glossary: dict[str, str] = field(default_factory=dict)  # 신고서 '전문용어 해설' 표: 용어 → 뜻 (훅·캡션 풀이의 근거)
    customers: list[str] = field(default_factory=list)  # 주요 고객사 이름 (원문 그대로, 한글 우선)
    customer_note: str | None = None  # 매출 비중 문장 (원문 숫자 그대로)
    # 주관사가 매긴 몸값: '인수인의 의견' 공모가 산정 표 (2026-10-28 '경쟁률 대신 공모가 → 주관사가 매긴 몸값')
    valuation_krw: int | None = None  # 주당 평가가액
    valuation_discount_low_pct: float | None = None  # 평가액 대비 할인율 (작은 쪽 = 밴드 상단)
    valuation_discount_high_pct: float | None = None  # (큰 쪽 = 밴드 하단)
    valuation_model: str | None = None  # 평가모형: PER, EV/EBITDA …
    valuation_multiple: float | None = None  # 적용 배수 (유사기업 평균)
    valuation_basis: str | None = None  # 적용 재무수치 원문: '추정치 (2027년 추정 당기순이익)'
    valuation_basis_year: int | None = None
    # [상장 후 유통가능 주식수 현황] 누적표: [{"months": 0(상장일)/1/3/…, "shares": 누적 주식 수, "pct": 비율}] (확약 해제편용)
    float_schedule: list[dict] = field(default_factory=list)
    derived: dict[str, str] = field(default_factory=dict)  # 계산값 → 공식
    found: dict[str, str] = field(default_factory=dict)  # 항목 → 어디서 찾았는지 (로그용)


# ── 증권발행실적보고서: 청약 결과와 상장일 (2026-10-28 티앤이코리아 '내일 상장' 게시물용) ──
def parse_offering_result(text: str) -> dict:
    """'3. 청약 및 배정현황' 표의 일반투자자 줄(배정 수량, 비율, 청약 건수, 청약 수량, 청약 금액)과
    '상장일(매매개시일) YYYY년 M월 D일', 균등 잔여주식 추첨 수량. 못 읽은 값은 빠진다"""
    t = body_text(text)
    out: dict = {}
    m = re.search(r"일반투자자\s*\|?\s*([\d,]+)\s*\|?\s*([\d.]+)\s*\|?\s*([\d,]+)\s*\|?\s*([\d,]+)\s*\|?\s*([\d,]+)", t)
    if m:
        alloc, apps, qty, amount = (_int(m.group(k)) for k in (1, 3, 4, 5))
        if alloc and qty and qty >= alloc:
            out.update(retail_alloc=alloc, retail_applicants=apps, retail_qty=qty, retail_amount_krw=amount)
    m = re.search(r"상장일\s*\(?\s*매매개시일\s*\)?\s*" + KDATE, t)
    if m:
        out["listing_date"] = _kdate(m.group(0))
    m = re.search(r"균등\s*잔여\s*주식\s*([\d,]+)\s*주는[^.]{0,60}?추첨", t)
    if m:
        out["even_lottery_shares"] = _int(m.group(1))
    return out


# ── 주요 고객사 (2026-10-28: '공시에 주요 고객사는 안 나와?' → 캡션 🏢에 원문 이름 그대로) ──

NAME = r"(?:[A-Z][A-Za-z0-9&.\-]*(?:\s[A-Z][A-Za-z0-9&.\-]*){0,3}|[가-힣][가-힣A-Za-z0-9]{1,20})"
_PARTICLES = ("으로부터", "로부터", "에서", "에게", "뿐", "향", "의", "는", "은", "에", "와", "과", "로")
_GENERIC = ("확보", "절감", "다변화", "계획", "기준", "시점", "물량", "절차", "보상", "안정", "품질", "발주", "공급", "생산", "원가",
            "비중", "매출", "고객", "업체", "시장", "산업", "사업", "제품", "부문", "기술", "장비", "소재", "정책", "거래", "경쟁")
_NAME_STOP = {"글로벌", "국내", "해외", "다수", "소수", "주요", "기존", "신규", "당사", "동사", "자사", "이들", "해당", "최종", "전방", "후방"}
_LATIN_STOP = {"OLED", "FOG", "RLG", "LCD", "IT", "AI", "IPO", "DART", "OEM", "ODM", "B2B", "R&D", "EMS", "LTE"}


def _strip(tok: str, t: str) -> str:
    """'삼성디스플레이의' → '삼성디스플레이'. 떼어 낸 꼴이 원문에 따로 더 많이 나올 때만 (에코프로의 '로'처럼 이름 끝 글자는 안 뗌)"""
    for p in _PARTICLES:
        if tok.endswith(p) and len(tok) - len(p) >= 2:
            s = tok[: -len(p)]
            if t.count(s) > t.count(tok) + 1:
                return s
    return tok


def customers_from_text(t: str) -> tuple[list[str], str]:
    """증권신고서의 주요 고객사 이름과 매출 비중 문장. 정해 둔 문장 꼴만 읽는다 (틀린 이름이 나가는 것보다 빠지는 게 낫다):
    ① '매출의 N% 이상이 X에서 발생' ② '주요 매출처인 X (및 Y)' ③ '주요 고객사는 … X, Y 등'.
    이름은 원문 그대로, '한글(영문)' 짝이 있으면 한글. A사·B사처럼 가린 이름은 뺀다."""
    alias, korean = {}, set()
    for ko, en in re.findall(r"([가-힣][가-힣A-Za-z0-9]*(?:\s[가-힣][가-힣A-Za-z0-9]*)?)\s*\(([A-Za-z][A-Za-z0-9 .&\-]{1,30})\)", t):
        words = ko.split()
        if len(words) == 2 and (words[0].endswith(("인", "의", "는", "은", "와", "과", "및")) or any(g in words[0] for g in _GENERIC)):
            ko = words[1]
        alias.setdefault(re.sub(r"\s", "", en).lower(), ko)
        korean.add(ko)
    marked = set(re.findall(r"([가-힣][가-힣A-Za-z0-9]{1,20})\s*(?:㈜|\(주\))", t))
    names, note = [], ""

    def add(raw: str, strict: bool):
        n = raw.strip()
        if re.fullmatch(r"[A-Z]사", n) or not n or n in _NAME_STOP:
            return
        if re.search(r"[가-힣]", n):
            n = _strip(n, t)
            if any(g in n for g in _GENERIC) or (strict and n not in korean and n not in marked and t.count(n) < 3):
                return
        else:
            if n in _LATIN_STOP or t.count(n) < 2:
                return
        n = alias.get(re.sub(r"\s", "", n).lower(), n)
        if 2 <= len(n) <= 25 and n not in names and n not in _NAME_STOP:
            names.append(n)

    m = re.search(r"매출의\s*(\d{1,3})\s*%\s*(이상|가량|내외|수준)?\s*(?:이|가)\s*(" + NAME + r")(?:㈜|\(주\))?\s*(?:에서|으로부터|로부터)\s*발생", t)
    if m:
        add(m.group(3), strict=False)
        if names:
            note = f"매출의 {m.group(1)}%{' ' + m.group(2) if m.group(2) else ''}이 {names[0]}에서 나옴"
    m2 = re.search(r"주요\s*매출처인\s+(" + NAME + r"(?:\s*(?:및|,)\s*" + NAME + r"){0,3})\s*향\s*매출은\s*각각\s*약?\s*(\d{1,3})\s*~\s*(\d{1,3})\s*%\s*대", t)
    if m2 and not note:
        group = [alias.get(re.sub(r"\s", "", x).lower(), _strip(x, t)) for x in re.split(r"\s*(?:및|,)\s*", m2.group(1)) if x.strip()]
        note = f"{'·'.join(group)} 매출 비중이 각각 약 {m2.group(2)}~{m2.group(3)}%대"
    for m in re.finditer(r"주요\s*(?:매출처|고객사)인\s+(" + NAME + r"(?:\s*(?:및|,)\s*" + NAME + r"){0,5})", t):
        for x in re.split(r"\s*(?:및|,)\s*", m.group(1)):
            add(x, strict=False)
    for m in re.finditer(r"주요\s*(?:고객사|매출처)(?:는|로는)\s+(.{5,300}?)\s*등(?:입니다|으로|이\s|과\s|와\s)", t):
        seg = re.sub(r"\s*\([^)]*\)", "", m.group(1))
        seg = re.sub(r"[가-힣 ]*?(?:장비사|업체|기업|회사|제조사)인\s+", ",", seg)
        for x in re.split(r"\s*(?:,|등과|및)\s*", seg):
            if re.fullmatch(NAME, x.strip() or "-"):
                add(x, strict=True)
    return names, note


# ── 주관사가 매긴 몸값 (증권신고서 '인수인의 의견 - 공모가격에 대한 의견') ──
# 표 서식이 회사마다 조금씩 다르다 (2026-10-28 동원파츠·옵토닉스·에스에프씨·티앤이코리아 원문 확인):
#   | 주당 평가가액 | 21,304원 | ...            | 상대가치 주당 평가가액 | 14,615원
#   | 평가액 대비 할인율 | 40.86%~28.18% |       | ④ | 주당 평가가액에 대한 할인율 | % | 29.84% ~ 19.17% | ...
#   | 평가모형 | EV/EBITDA                       | 적용 재무수치 | 추정치 (2027년 추정 당기순이익)
#   | 적용 PER | 19.48배 |                       | 적용 EV/EBITDA 거래배수 | 13.39 배
# 발행조건확정 신고서는 고친 부분만 '(주21) 정정 전' / '(주21) 정정 후' 짝으로 싣는다. 정정 전 값은 버린다.
_AMEND = re.compile(r"^(?:\(주\d+\)\s*)?[\[(<]?\s*정정\s*(전|후)\s*[\])>]?$")
_VAL_CELL = re.compile(r"^(?:상대가치\s*)?주당\s*평가\s*가액$")
_DISC_CELL = re.compile(r"평가액\s*대비\s*할인율|주당\s*평가\s*가액에\s*대한\s*할인율")
_MULT_CELL = re.compile(r"^(?:적용|유사기업|유사회사)\s*(?:평균\s*)?(PER|EV/EBITDA|PSR|PBR)(?:\s*(?:거래)?배수)?$")
_PCT_RANGE = re.compile(r"(\d{1,2}(?:\.\d+)?)\s*%\s*~\s*(\d{1,2}(?:\.\d+)?)\s*%")


def _amend_flags(lines: list[str]) -> list[bool]:
    """줄마다 정정표의 '정정 전' 쪽인지 (가장 가까운 위쪽 표시, 800줄 안). 한 번 훑어서 만든다"""
    flags, state, at = [], None, -1
    for i, ln in enumerate(lines):
        m = _AMEND.match(ln.strip())
        if m:
            state, at = m.group(1), i
        flags.append(state == "전" and i - at <= 800)
    return flags


def parse_valuation(lines: list[str]) -> dict:
    """주당 평가가액·할인율·평가모형·적용 배수·적용 재무수치. 여러 번 나오면 정정 전이 아닌 마지막 값"""
    out: dict = {}
    before = _amend_flags(lines)
    for i, ln in enumerate(lines):
        if "|" not in ln or before[i]:
            continue
        c = _cells(ln)
        for j, cell in enumerate(c):
            rest = c[j + 1:j + 4]
            if _VAL_CELL.match(cell):
                for x in rest:
                    m = re.fullmatch(r"([\d,]{4,})\s*원?", x)
                    if m and (x.endswith("원") or "원" in rest):
                        out["valuation_krw"] = _int(m.group(1))
                        break
            elif _DISC_CELL.search(cell):
                m = _PCT_RANGE.search(" | ".join(c[j + 1:]))
                if m:
                    a, b = float(m.group(1)), float(m.group(2))
                    out["valuation_discount_low_pct"], out["valuation_discount_high_pct"] = min(a, b), max(a, b)
            elif cell == "평가모형" and rest and rest[0]:
                out["valuation_model"] = rest[0]
            elif cell.replace(" ", "") == "적용재무수치" and rest and rest[0]:
                out["valuation_basis"] = rest[0]
                y = re.search(r"(20\d{2})\s*년", rest[0])
                out["valuation_basis_year"] = int(y.group(1)) if y else None
            elif _MULT_CELL.match(cell):
                for x in rest:
                    m = re.fullmatch(r"(\d{1,3}(?:\.\d+)?)\s*(?:배|x|X)?", x)
                    if m:
                        out["valuation_multiple"] = float(m.group(1))
                        break
    if "valuation_discount_low_pct" not in out:  # 표가 없으면 문장: '주당 평가가액을 기초로 하여 29.84% ~ 19.17%의 할인율'
        for i, ln in enumerate(lines):
            m = re.search(r"평가가액을\s*기초로\s*하여\s*" + _PCT_RANGE.pattern, ln)
            if m and not before[i]:
                a, b = float(m.group(1)), float(m.group(2))
                out["valuation_discount_low_pct"], out["valuation_discount_high_pct"] = min(a, b), max(a, b)
    return out


def parse_float_schedule(lines: list[str]) -> list[dict]:
    """'[상장 후 유통가능 주식수 현황]' 누적표 (2026-10-28 확약 해제편). 여러 번 나오면 정정 전이 아닌 마지막 표:
    | 상장일 유통가능 | 3,551,856 | 31.29%   | 상장 후 1개월 뒤 유통가능 | 5,337,354 | 47.02%  …
    신고서 주석대로 '공모 주주의 의무보유 확약은 고려하지 않은' 숫자다"""
    before = _amend_flags(lines)
    tables: list[list[dict]] = []
    cur: list[dict] = []
    for i, ln in enumerate(lines):
        c = _cells(ln) if ln.startswith("|") else []
        head = c[0].replace(" ", "") if c else ""
        m = re.fullmatch(r"상장(?:일|후(\d+)(개월|년)뒤)유통가능", head)
        if m and len(c) >= 3 and _int(c[1]) and not before[i]:
            months = 0 if not m.group(1) else int(m.group(1)) * (12 if m.group(2) == "년" else 1)
            pc = _float(c[2])
            if months == 0 and cur:
                tables.append(cur)
                cur = []
            cur.append({"months": months, "shares": _int(c[1]), "pct": pc})
    if cur:
        tables.append(cur)
    good = [t for t in tables if t and t[0]["months"] == 0 and len(t) >= 2]
    return good[-1] if good else []


def parse(text: str) -> DocFacts:
    t = body_text(text)
    lines = t.split("\n")
    f = DocFacts()

    for k, v in parse_valuation(lines).items():
        setattr(f, k, v)
    f.float_schedule = parse_float_schedule(lines)
    if f.float_schedule:  # 상장일 유통 가능 비율은 정정 후 누적표 값이 가장 새 값 (아래 표 찾기보다 먼저)
        f.float_ratio_pct = f.float_schedule[0]["pct"]
        f.found["float"] = "상장 후 유통가능 주식수 현황 (정정 후)"
    if f.valuation_krw:
        f.found["valuation"] = f"{f.valuation_krw:,}원, 할인율 {f.valuation_discount_high_pct}~{f.valuation_discount_low_pct}%"

    # 시장
    if re.search(r"코스닥시장\s*(신규)?상장", t):
        f.market = "코스닥"
    elif re.search(r"유가증권시장\s*(신규)?상장", t):
        f.market = "코스피"

    # 업종 한 구절: "당사가 영위하는 AI 인프라 플랫폼 사업은"
    m = re.search(r"당사가 영위하는 ([^\s|「]{1,20}(?: [^\s|「]{1,14}){0,2}?) ?사업", t)
    if m and m.group(1) not in ("사업",):
        f.industry = m.group(1).strip()
        f.found["industry"] = "당사가 영위하는 … 사업"

    # 공모가 밴드·확정가
    m = re.search(r"(?:희망공모가액|공모희망가액)[^\d\n]{0,14}([\d,]{4,})\s*원?\s*~\s*([\d,]{4,})\s*원", t)
    if m:
        f.band_low_krw, f.band_high_krw = _int(m.group(1)), _int(m.group(2))
        f.found["band"] = m.group(0)[:60]
    # "확정공모가액을 19,000원으로 (최종) 결정" 처럼 결정 문장만 인정. 수요예측 전 신고서에는 확정가가 없다
    m = re.search(r"확정공모가액(?:을|은|는|이)?\s*([\d,]{4,})\s*원\s*으로", t)
    if m:
        f.final_price_krw = _int(m.group(1))
        f.found["final_price"] = m.group(0)

    # 공모개요 표: 증권수량 / 모집총액 / 청약기일·납입기일 / 주관사
    i = _find(lines, lambda s: s.startswith("1. 공모개요"))
    if i >= 0:
        for j in range(i, min(i + 40, len(lines))):
            c = _cells(lines[j])
            if len(c) >= 6 and c[0] in ("보통주", "기명식 보통주") and _int(c[1]):
                f.shares_offered, f.offering_amount_krw = _int(c[1]), _int(c[4])
                f.found["offering"] = lines[j][:80]
            if c and c[0] in ("대표주관회사", "공동주관회사", "인수회사", "공동대표주관회사") and len(c) > 1:
                if c[1] not in f.underwriters:
                    f.underwriters.append(c[1])
            if c[:2] == ["청약기일", "납입기일"] and j + 1 < len(lines):
                v = _cells(lines[j + 1])
                if v:
                    f.subscription_start, f.subscription_end = _krange(v[0])
                    f.payment_date = _kdate(v[1]) if len(v) > 1 else None
                    f.found["subscription"] = lines[j + 1][:80]

    # 환불일: "... 주금납입기일 당일 2026년 10월 27일(화)에 환불합니다"
    m = re.search(KDATE + r"\s*(?:\([^)]*\))?\s*에\s*환불", t)
    if m:
        f.refund_date = _kdate(m.group(0))
        f.found["refund"] = m.group(0)
    elif f.payment_date:
        f.refund_date = f.payment_date
        f.found["refund"] = "납입기일과 같다고 가정"

    # 상장 예정일 (명시된 경우만)
    m = re.search(r"상장\s*\(?예정\)?일[^\d\n]{0,20}" + KDATE, t)
    if m:
        f.listing_date = _kdate(m.group(0))
        f.found["listing"] = m.group(0)

    # 수요예측 기간·확정가 공고일
    m = re.search(r"수요예측\s*(?:예정)?일(?:은|:)?\s*" + KDATE + r"[^~\n]{0,8}~\s*(?:\d{4}\s*년\s*)?(\d{1,2})\s*월\s*(\d{1,2})\s*일", t)
    if m:
        f.forecast_start = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
        f.forecast_end = f"{m.group(1)}-{int(m.group(4)):02d}-{int(m.group(5)):02d}"
        f.found["forecast"] = m.group(0)
    for ln in lines:
        c = _cells(ln)
        if len(c) >= 2 and c[0].replace(" ", "") == "모집가액확정의공고":
            f.final_price_date = _kdate(c[1])
            f.found["final_price_date"] = ln[:60]
            break

    # 수요예측 경쟁률: "| 경쟁률(주2) | ... | 284.53" 마지막 셀
    for ln in lines:
        c = _cells(ln)
        if c and c[0].startswith("경쟁률") and len(c) >= 3:
            v = _float(c[-1])
            if v:
                f.demand_ratio = v
                f.found["demand_ratio"] = ln[:80]
                break

    # 의무보유 확약 비율: 확약기간별 참여내역의 합계표
    i = _find(lines, lambda s: re.search(r"의무보유\s*확약\s*(기간별|신청\s*내역|참여\s*내역)", s) is not None)
    if i >= 0:
        total_idx = -1
        for j in range(i, min(i + 80, len(lines))):
            c = _cells(lines[j])
            # 마지막 합계(외국+합계 표). 단 바로 윗줄이 '…확약'(보통 '미확약')인 합계만: 80줄 안에 다른 표(배정 등)의 합계가 있으면
            # 그걸 잡아 확약 0%로 읽던 문제 (2026-10-29 인텔리빅스: 실제 6.14%)
            if c and c[0].replace(" ", "") == "합계" and len(c) >= 3 and j > i and "확약" in (_cells(lines[j - 1]) or [""])[0]:
                total_idx = j
        if total_idx > 0:
            # 표 머리에 '합계' 열이 있으면 마지막 (건수|수량|신청가격) 묶음의 수량 = 합계. 없으면(국내 표만) 묶음별 수량을 더한다
            header_total = any("합계" in ln.replace(" ", "") and "구분" in ln.replace(" ", "") for ln in lines[max(i, total_idx - 10):total_idx])

            def qty(c: list[str]) -> int:
                if header_total:
                    return _int(c[-2]) or 0
                return sum(_int(x) or 0 for x in c[2::3])

            total = qty(_cells(lines[total_idx]))
            locked = 0
            rows = []
            for j in range(total_idx - 1, max(i, total_idx - 8), -1):
                c = _cells(lines[j])
                if c and "확약" in c[0] and "미확약" not in c[0].replace(" ", ""):  # '1. 미확약'처럼 번호가 붙어도 뺀다 (2026-10-28 티앤이코리아 100%로 잘못 읽음)
                    q = qty(c)
                    locked += q
                    rows.append(f"{c[0]}={q}")
            if total:
                r = locked / total * 100
                # 0.01% 미만은 0%로 반올림되면 '확약 0%'로 오해되니 유효숫자 1자리 (2026-10-29 크리에이츠 5,000주 = 0.002%)
                f.lockup_ratio_pct = round(r, 2) if r == 0 or r >= 0.01 else float(f"{r:.1g}")
                f.derived[f"{f.lockup_ratio_pct:g}"] = f"lockup: ({' + '.join(rows)}) / {total} * 100"
                f.found["lockup"] = f"합계 수량 {total}, 확약 {locked}"

    # 유통 가능 물량 (누적표에서 못 읽었을 때)
    for ln in lines if f.float_ratio_pct is None else []:
        c = _cells(ln)
        if c and c[0].replace(" ", "") == "상장일유통가능" and len(c) >= 3:
            pc = [x for x in c[1:] if "%" in x] or [x for x in c[1:] if re.fullmatch(r"\d{1,2}\.\d+", x.strip())]
            f.float_ratio_pct = _float(pc[0]) if pc else None
            f.found["float"] = ln[:80]
            break
    if f.float_ratio_pct is None:
        # 문장형: "N주(공모 후 25.46%)는 상장 직후 유통 가능한 물량" / "N주 중 23.95%에 해당하는 N주는 상장 직후 유통가능 물량"
        m = re.search(r"([\d.]+)\s*%\s*\)?[^%\n]{0,60}?상장\s*직후[^.\n]{0,30}?유통\s*가능", t)
        if m:
            f.float_ratio_pct = float(m.group(1))
            f.found["float"] = m.group(0)[-90:]

    # 최대주주 공모 후 지분율
    for ln in lines:
        if "최대주주(본인)" in ln:
            pcts = re.findall(r"([\d.]+)%", ln)
            if len(pcts) >= 2:
                f.major_holder_after_pct = float(pcts[1])
                f.found["major_holder"] = ln[:80]
            break

    # 최소 청약 단위·증거금률
    m = re.search(r"\|\s*([\d,]+)\s*주\s*이상\s*~\s*[\d,]+\s*주\s*이하\s*\|\s*([\d,]+)\s*주", t)
    if m:
        f.min_shares = _int(m.group(1))
        f.found["min_shares"] = m.group(0)
    m = re.search(r"일반청약자의\s*청약증거금은\s*청약금액의\s*(\d+)\s*%", t)
    if m:
        f.deposit_rate_pct = int(m.group(1))
    if f.min_shares and f.final_price_krw and f.deposit_rate_pct:
        f.min_deposit_krw = f.min_shares * f.final_price_krw * f.deposit_rate_pct // 100
        f.derived[f"{f.min_deposit_krw:,}"] = f"min_deposit: {f.min_shares} * {f.final_price_krw} * {f.deposit_rate_pct}%"

    # 실적: 요약재무정보 블록에서 매출액 행을 찾고, 바로 위 기간 헤더에서 '결산 연도' 열(반기·분기 제외)을 고른다
    i = _find(lines, lambda s: re.match(r"1\.\s*요약\s*재무\s*정보", s) is not None)
    if i >= 0:
        for j in range(i, min(i + 120, len(lines))):
            c = _cells(lines[j])
            head0 = re.sub(r"^[ⅠⅡⅢⅣⅤIVX\d.\s()-]+", "", c[0]).replace(" ", "") if c else ""
            if head0 not in ("매출액", "영업수익", "수익(매출액)", "매출", "매출액(영업수익)"):
                continue
            col, year = None, None
            for k in range(j - 1, max(i, j - 30), -1):  # 위쪽 가장 가까운 기간 헤더
                h = _cells(lines[k])
                yrs = [bool(re.search(r"(19|20)\d{2}", x)) for x in h[1:]]
                if len(h) >= 3 and sum(yrs) >= 2:
                    off = 0 if re.search(r"(19|20)\d{2}", h[0]) else 1  # 머리 셀(구분/과목/빈칸)이 있으면 값은 1번째부터
                    for idx, x in enumerate(h[off:]):
                        if re.search(r"반기|분기", x):
                            continue
                        if _full_year(x):
                            col, year = idx + 1, int(re.search(r"((?:19|20)\d{2})", x).group(1))
                            break
                    break
            if col is None or len(c) <= col:
                continue
            unit = 1
            for k in range(j, max(i - 1, j - 40), -1):
                um = re.search(r"단위\s*:?\s*(백만원|천원|원)", lines[k])
                if um:
                    unit = {"백만원": 1_000_000, "천원": 1_000, "원": 1}[um.group(1)]
                    break
            rv = _int(c[col])
            if rv is None:
                continue
            f.revenue_krw, f.fiscal_year = rv * unit, year
            f.found["revenue"] = f"{lines[j][:70]} (열 {col}, 단위 {unit})"
            # 교차 확인: 같은 숫자가 본문 다른 곳(손익계산서 등)에도 있어야 한다. 열을 잘못 고르면 보통 1번만 나온다
            f.found["revenue_occurrences"] = str(t.count(c[col].strip()))
            for k in range(j + 1, min(j + 12, len(lines))):
                cc = _cells(lines[k])
                h2 = re.sub(r"^[ⅠⅡⅢⅣⅤIVX\d.\s()-]+", "", cc[0]).replace(" ", "") if cc else ""
                if (h2.startswith("영업이익") or h2.startswith("영업손실")) and len(cc) > col and _int(cc[col]) is not None:
                    v = _int(cc[col]) * unit
                    f.operating_income_krw = -abs(v) if h2.startswith("영업손실") else v
                    f.found["operating_income"] = lines[k][:70]
                    break
            break

    # 자금의 사용목적: "시설자금 | ... | 계" 표 + 단위
    i = _find(lines, lambda s: s.startswith("V. 자금의 사용목적"))
    if i >= 0:
        unit = 1
        for j in range(i, min(i + 60, len(lines))):
            c = _cells(lines[j])
            um = re.search(r"단위\s*:\s*(백만원|천원|원)", lines[j])
            if um:
                unit = {"백만원": 1_000_000, "천원": 1_000, "원": 1}[um.group(1)]
            if "시설자금" in c and "계" in c and j + 1 < len(lines):
                vals = [v for v in re.split(r"\s*\|\s*|\s+", lines[j + 1].strip().strip("|")) if v]
                if len(vals) == len(c):
                    for name, v in zip(c, vals):
                        amt = _int(v)
                        if name != "계" and amt:
                            f.use_of_proceeds.append((name, amt * unit))
                    f.found["use_of_proceeds"] = f"{lines[j][:60]} (단위 {unit})"
                break

    # 사업 문장: '1. 사업의 개요' ~ '2. 주요 제품' 사이 본문(표 제외)
    i = _find(lines, lambda s: s.startswith("1. 사업의 개요"))
    if i >= 0:
        j = _find(lines, lambda s: re.match(r"2\.\s*주요\s*제품", s) is not None, i)
        chunk = [ln for ln in lines[i + 1:(j if j > 0 else i + 80)] if not ln.startswith("|")]
        for para in chunk:
            for sent in re.split(r"(?<=다\.)\s+", para):
                sent = sent.strip()
                if 25 <= len(sent) <= 300 and sent.endswith("다.") and not re.search(r"\.(jpg|png|gif)", sent):
                    f.business_evidence.append(sent)
        f.business_evidence = f.business_evidence[:25]

    # 전문용어 해설 표 ('용어 | 내용'): 회사가 직접 쓴 쉬운 뜻풀이. 훅의 비유와 캡션 ':' 풀이 줄의 근거로 쓴다
    i = _find(lines, lambda s: "전문용어" in s and "해설" in s)
    if i >= 0:
        for ln in lines[i + 1:i + 250]:
            if not ln.startswith("|"):
                if f.glossary:
                    break
                continue
            c = [x for x in _cells(ln) if x]
            if len(c) >= 2 and c[0] not in ("용어", "용어명", "구분") and 4 <= len(c[-1]) <= 200:
                f.glossary[c[0]] = c[-1]
        if f.glossary:
            f.found["glossary"] = f"{len(f.glossary)}개"

    # 주요 고객사와 매출 비중
    try:
        f.customers, note = customers_from_text(t)
        f.customer_note = note or None
        if f.customers:
            f.found["customers"] = ", ".join(f.customers[:6]) + (f" / {note}" if note else "")
    except Exception:  # noqa: BLE001  고객사는 덤이라 실패해도 나머지는 그대로
        pass

    # 위험: 핵심투자위험 표의 사업위험/회사위험 첫 항목
    i = _find(lines, lambda s: "핵심투자위험" in s)
    if i >= 0:
        for cat in ("회사위험", "사업위험"):
            k = _find(lines, lambda s, cat=cat: s.startswith(f"| {cat} |"), i, 400)
            if k < 0:
                continue
            seg = _cells(lines[k])[-1]
            for extra in lines[k + 1:k + 4]:  # 제목만 있고 본문이 다음 줄(BR)로 이어지는 서식
                if extra.startswith("|"):
                    break
                seg += " " + extra
            m = re.match(r"[가-힣]\.\s*(.{4,70}?위험)\s*(.+)", seg)
            if m:
                title, body = m.group(1).strip(), m.group(2).strip()
                # 본문은 자르지 않고 앞 3문장(최대 600자)을 원문 그대로 둔다. 카드에는 LLM 요약 2줄이 들어가고, 이 본문은 근거 대조용
                sents = re.split(r"(?<=다\.)\s+", body)
                body = " ".join(sents[:3])[:600]
                f.risks.append((cat, title, body))
    return f
