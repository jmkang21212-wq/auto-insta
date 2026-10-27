"""DART 오픈API 어댑터.

확인된 엔드포인트와 필드명 (2026-10-26 실제 응답으로 확인):
- 공시검색 list.json: corp_code, corp_name, stock_code, corp_cls, report_nm, rcept_no, flr_nm, rcept_dt, rm
- 증권신고서(지분증권) estkRs.json: group[].title 별 list
    일반사항:        sbd(청약기일 "YYYY년 MM월 DD일 ~ ..."), pymd(납입기일), sband(배정공고일), asand(배정기준일)
    증권의종류:      stksen, stkcnt(주식 수), fv(액면가), slprc(모집가액), slta(모집총액), slmthn(모집방법)
    인수인정보:      actsen(대표/공동), actnmn(인수인), udtcnt, udtamt, udtprc, udtmth
    자금의사용목적:  se(구분), amt(금액)   ※ 2026-10-27 래블업 건에서 원문(백만원 표)과 10배 차이 → 원문 우선, API는 폴백
- 원문 document.xml: zip 안에 XML 1개. 구조화 API에 없는 항목은 adapters/dart_doc.py 가 여기서 뽑습니다.
    공모가 밴드, 확정가, 경쟁률, 확약 비율, 유통 가능 물량, 최대주주 지분, 환불일, 최소 청약, 실적, 사업 문장, 위험

문서 선택: 같은 회사의 최근 150일 발행공시 중 증권신고서(지분증권)·투자설명서를 새 것부터 최대 3건 읽어
비어 있는 항목만 이전 문서로 채웁니다. [발행조건확정] 정정신고서는 바뀐 부분만 담고 있어서(사업 내용·위험 없음),
같은 날 나오는 [기재정정] 투자설명서가 보통 가장 완전합니다.

estkRs.json은 접수번호가 아니라 회사+기간으로 조회되고, 정정 건은 원 신고서의 접수번호로 묶여 나옵니다.
"""
from __future__ import annotations

import io
import re
import time
import zipfile
from datetime import date, timedelta

import requests

from gongsi.adapters.base import Adapter
from gongsi.adapters.dart_doc import DocFacts, parse, to_text
from gongsi.logos import homepage_from_text
from gongsi.models import Disclosure, IpoFacts, Risk, UseOfProceeds

BASE = "https://opendart.fss.or.kr/api"

REPORT_TYPE_RULES = [
    ("증권신고서(지분증권)", "ipo"),
    ("유상증자결정", "capital_increase"),
    ("전환사채권발행결정", "cb"),
    ("신주인수권부사채권발행결정", "bw"),
    ("주식등의대량보유상황보고서", "major_holder"),
    ("임원ㆍ주요주주특정증권등소유상황보고서", "insider"),
]

MARKET = {"Y": "코스피", "K": "코스닥", "N": "코넥스", "E": None}  # E(기타) = 비상장 → 원문에서 상장 시장을 읽음


def classify(report_nm: str, corp_name: str = "", corp_cls: str = "") -> str:
    for key, t in REPORT_TYPE_RULES:
        if key in report_nm:
            if t == "ipo":
                # 증권신고서(지분증권)은 공모주만이 아니다: 스팩, 상장사의 공모 유상증자도 같은 서식
                if "기업인수목적" in corp_name or "스팩" in corp_name:
                    return "spac"
                if corp_cls in ("Y", "K", "N"):
                    return "rights_offering"
            return t
    return "other"


def _num(s: str | None) -> int | None:
    if not s or s.strip() in ("-", ""):
        return None
    digits = re.sub(r"[^\d]", "", s)
    return int(digits) if digits else None


def _kdate(s: str | None) -> str | None:
    """'2026년 11월 24일' → '2026-11-24'"""
    if not s:
        return None
    m = re.search(r"(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일", s)
    return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}" if m else None


def _krange(s: str | None) -> tuple[str | None, str | None]:
    if not s:
        return None, None
    parts = [p.strip() for p in s.split("~")]
    start = _kdate(parts[0])
    end = _kdate(parts[1]) if len(parts) > 1 else start
    return start, end


def _ymd(s: str) -> str:
    return f"{s[:4]}-{s[4:6]}-{s[6:]}"


class DartAdapter(Adapter):
    name = "dart"
    account = "kr"

    def __init__(self, api_key: str, session: requests.Session | None = None):
        if not api_key:
            raise ValueError("DART_API_KEY가 비어 있습니다")
        self.key = api_key
        self.s = session or requests.Session()
        self.calls = 0  # 일일 한도(10,000으로 알려짐, 확인 필요) 추적용

    def _get(self, path: str, **params) -> dict:
        # DART가 가끔 빈 본문·HTML을 돌려주거나 연결을 끊는다 (2026-10-27 관측). 짧게 쉬고 2번까지 재시도
        for attempt in range(3):
            self.calls += 1
            try:
                r = self.s.get(f"{BASE}/{path}", params={"crtfc_key": self.key, **params}, timeout=20)
                r.raise_for_status()
                data = r.json()
                break
            except (ValueError, requests.RequestException) as e:
                if attempt == 2:
                    raise RuntimeError(f"DART {path} 응답 오류 3회: {type(e).__name__}: {e}") from e
                time.sleep(2 * (attempt + 1))
        if data.get("status") not in ("000", "013"):  # 013 = 조회 결과 없음
            raise RuntimeError(f"DART {path} {data.get('status')}: {data.get('message')}")
        return data

    # --- 공시 목록 ---------------------------------------------------------
    def _rows(self, date_from: str, date_to: str, corp_code: str | None = None) -> list[dict]:
        rows: list[dict] = []
        page = 1
        while True:
            params = dict(bgn_de=date_from.replace("-", ""), end_de=date_to.replace("-", ""),
                          pblntf_ty="C", page_no=page, page_count=100)  # C = 발행공시
            if corp_code:
                params["corp_code"] = corp_code
            data = self._get("list.json", **params)
            rows += data.get("list", [])
            if page >= int(data.get("total_page", 1) or 1):
                return rows
            page += 1

    def list_disclosures(self, date_from: str, date_to: str) -> list[Disclosure]:
        out: list[Disclosure] = []
        for row in self._rows(date_from, date_to):
            name = row["report_nm"]
            out.append(Disclosure(
                rcept_no=row["rcept_no"], corp_code=row["corp_code"],
                corp_name=row["corp_name"], report_type=classify(name, row["corp_name"], row.get("corp_cls", "")),
                report_name=name, filed_at=_ymd(row["rcept_dt"]),
                parent_rcept_no=None,  # 정정 공시의 원 공시 연결은 미구현
                market=MARKET.get(row.get("corp_cls", ""), None),
            ))
        return out

    # --- 원문 -------------------------------------------------------------------
    def document_xml(self, rcept_no: str) -> str:
        for attempt in range(3):
            self.calls += 1
            try:
                r = self.s.get(f"{BASE}/document.xml", params={"crtfc_key": self.key, "rcept_no": rcept_no}, timeout=60)
                r.raise_for_status()
                break
            except requests.RequestException as e:
                if attempt == 2:
                    raise
                time.sleep(2 * (attempt + 1))
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            name = next(n for n in z.namelist() if n.lower().endswith(".xml"))
            return z.read(name).decode("utf-8", errors="ignore")

    def document_text(self, rcept_no: str) -> str:
        return to_text(self.document_xml(rcept_no))

    def _doc_candidates(self, d: Disclosure) -> list[dict]:
        """같은 회사의 증권신고서·투자설명서, 새 것부터. d 자신이 맨 앞."""
        end = date.fromisoformat(d.filed_at) if d.filed_at else date.today()
        rows = self._rows((end - timedelta(days=150)).isoformat(), end.isoformat(), d.corp_code)
        docs = [r for r in rows if ("증권신고서(지분증권)" in r["report_nm"] or "투자설명서" in r["report_nm"])
                and r["rcept_no"] != d.rcept_no]
        docs.sort(key=lambda r: r["rcept_no"], reverse=True)
        me = {"rcept_no": d.rcept_no, "report_nm": d.report_name or "", "rcept_dt": (d.filed_at or "").replace("-", "")}
        # 같은 날 투자설명서가 있으면 그것을 먼저(더 완전함), 그 다음 자기 자신, 그 다음 과거 문서
        same_day = [r for r in docs if r["rcept_dt"] == me["rcept_dt"] and "투자설명서" in r["report_nm"]]
        rest = [r for r in docs if r not in same_day]
        return same_day + [me] + rest

    # --- 증권신고서(지분증권) ----------------------------------------------------
    def ipo_facts(self, d: Disclosure) -> IpoFacts:
        merged = DocFacts()
        read: list[str] = []
        key_fields = ("final_price_krw", "demand_ratio", "lockup_ratio_pct", "float_ratio_pct", "business_evidence", "risks", "revenue_krw")
        homepage: str | None = None
        for r in self._doc_candidates(d)[:3]:
            try:
                txt = to_text(self.document_xml(r["rcept_no"]))
                homepage = homepage or homepage_from_text(txt)
                got = parse(txt)
            except Exception as e:  # 원문이 없거나 zip이 아닌 경우: 다음 문서로
                read.append(f"{r['rcept_no']}:error:{type(e).__name__}")
                continue
            read.append(f"{r['rcept_no']}:{r['report_nm']}")
            for k, v in got.__dict__.items():
                cur = getattr(merged, k)
                if k in ("derived", "found", "glossary"):
                    cur.update({kk: vv for kk, vv in v.items() if kk not in cur})
                elif cur in (None, [], "") and v not in (None, [], ""):
                    setattr(merged, k, v)
            if all(getattr(merged, k) not in (None, []) for k in key_fields):
                break

        # 구조화 API: 원문에서 못 채운 기본 항목의 폴백
        api: dict = {}
        try:
            end = date.fromisoformat(d.filed_at) if d.filed_at else date.today()
            api = self._get("estkRs.json", corp_code=d.corp_code,
                            bgn_de=(end - timedelta(days=150)).strftime("%Y%m%d"), end_de=end.strftime("%Y%m%d"))
        except Exception as e:
            read.append(f"estkRs:error:{type(e).__name__}")
        groups = {g["title"]: g.get("list", []) for g in api.get("group", [])}
        latest = lambda rows: sorted(rows, key=lambda r: r.get("rcept_no", ""))[-1] if rows else {}
        general = latest(groups.get("일반사항", []))
        kinds = [k for k in groups.get("증권의종류", []) if "보통주" in k.get("stksen", "")]
        common = latest(kinds)
        api_underwriters = list(dict.fromkeys(r["actnmn"] for r in groups.get("인수인정보", []) if r.get("actnmn") not in (None, "-")))
        api_uses = [UseOfProceeds(purpose=r["se"], amount_krw=_num(r["amt"]) or 0)
                    for r in groups.get("자금의사용목적", []) if _num(r.get("amt")) and r.get("se") != "발행제비용"]
        sub_start, sub_end = _krange(general.get("sbd"))

        # 홈페이지: 원문 표지에 없으면(투자설명서) 기업개황 company.json 의 hm_url (2026-10-27 확인: 'www.lablup.com/')
        if not homepage:
            try:
                hm = self._get("company.json", corp_code=d.corp_code).get("hm_url") or ""
                hm = hm.strip()
                if hm:
                    homepage = hm if hm.startswith("http") else "http://" + hm
            except Exception as e:
                read.append(f"company:error:{type(e).__name__}")

        # 확정가 공고일: 원문 표 → [발행조건확정] 접수일
        final_price_date = merged.final_price_date
        if not final_price_date:
            fixed = [r for r in self._doc_candidates(d) if "발행조건확정" in r["report_nm"] and r.get("rcept_dt")]
            if fixed:
                final_price_date = _ymd(fixed[0]["rcept_dt"])

        uses = [UseOfProceeds(purpose=n, amount_krw=a) for n, a in merged.use_of_proceeds] or api_uses
        return IpoFacts(
            corp_name=d.corp_name, industry=merged.industry, market=merged.market or d.market, homepage=homepage,
            band_low_krw=merged.band_low_krw, band_high_krw=merged.band_high_krw,
            # API의 slprc는 수요예측 전엔 밴드 하단(예정가)이라 확정가로 쓰면 안 된다. 경쟁률이 있을 때만 폴백
            final_price_krw=merged.final_price_krw or (_num(common.get("slprc")) if merged.demand_ratio is not None else None),
            shares_offered=merged.shares_offered or _num(common.get("stkcnt")),
            offering_amount_krw=merged.offering_amount_krw or _num(common.get("slta")),
            demand_ratio=merged.demand_ratio, lockup_ratio_pct=merged.lockup_ratio_pct,
            float_ratio_pct=merged.float_ratio_pct, major_holder_after_pct=merged.major_holder_after_pct,
            forecast_start=merged.forecast_start, forecast_end=merged.forecast_end, final_price_date=final_price_date,
            subscription_start=merged.subscription_start or sub_start, subscription_end=merged.subscription_end or sub_end,
            refund_date=merged.refund_date or _kdate(general.get("pymd")), listing_date=merged.listing_date,
            underwriters=merged.underwriters or api_underwriters,
            min_shares=merged.min_shares, deposit_rate_pct=merged.deposit_rate_pct, min_deposit_krw=merged.min_deposit_krw,
            fiscal_year=merged.fiscal_year, revenue_krw=merged.revenue_krw, operating_income_krw=merged.operating_income_krw,
            use_of_proceeds=sorted(uses, key=lambda u: -u.amount_krw),
            risks=[Risk(category=c, title=t, quote=q) for c, t, q in merged.risks],
            business_evidence=merged.business_evidence, glossary=merged.glossary,
            raw={"documents": read, "found": merged.found, "derived": merged.derived, "api_calls": self.calls},
        )
