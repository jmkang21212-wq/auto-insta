"""실행 흐름: 수집 → 필터·선정 → 추출(필드 + LLM 요약) → 검증 → 카드 생성 → (게시는 3단계).

사용법
  python -m gongsi.pipeline --sample                 # 키 없이 픽스처로
  python -m gongsi.pipeline --date 2026-10-26        # DART 실제 공시 (DART_API_KEY 필요)
  python -m gongsi.pipeline --rcept-no 2026...       # 특정 증권신고서 1건만
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

from gongsi import cards as cardmod
from gongsi.adapters.base import Adapter
from gongsi.config import settings
from gongsi.db import DB
from gongsi.models import Disclosure
from gongsi.logos import fetch_logo
from gongsi.render import render
from gongsi.select import select
from gongsi.summarize import summarize
from gongsi.verify import check_caption_layout, check_numbers, check_forbidden, check_risks, check_summary


def log(stage: str, **kw):
    print(json.dumps({"stage": stage, **kw}, ensure_ascii=False), flush=True)


def run(adapter: Adapter, date_from: str, date_to: str, only_rcept: str | None, today: date) -> int:
    db = DB(settings.db_path)
    run_id = db.start_run(adapter.account, "ipo")
    source_id = db.ensure_source(adapter.account, adapter.name, type(adapter).__name__)
    produced = 0
    try:
        # 1. 수집
        db.stage(run_id, "collect")
        try:
            disclosures = adapter.list_disclosures(date_from, date_to)
            db.source_ok(source_id)
        except Exception as e:
            db.source_fail(source_id)
            raise
        if only_rcept:
            disclosures = [d for d in disclosures if d.rcept_no == only_rcept]
        log("collect", count=len(disclosures))

        # 2~3. 필터·선정
        db.stage(run_id, "select")
        scored = select(disclosures)
        issue_ids: dict[str, int] = {}
        for s in scored:
            did = db.upsert_disclosure(run_id, source_id, s.disclosure.model_dump())
            issue_ids[s.disclosure.rcept_no] = db.add_issue(run_id, did, s.score, s.selected, s.standalone, s.excluded_reason)
        chosen = [s for s in scored if s.selected]
        log("select", selected=[(s.disclosure.corp_name, s.disclosure.report_type, s.score) for s in chosen],
            excluded=[(s.disclosure.corp_name, s.excluded_reason) for s in scored if s.excluded_reason])
        if not chosen:
            db.finish_run(run_id, "no_issue")
            return 0

        for s in chosen:
            d: Disclosure = s.disclosure
            if d.report_type != "ipo":
                log("skip", corp=d.corp_name, reason="1단계는 공모주만 카드 생성, 다른 유형은 2단계")
                continue

            # 이미 게시 대기열(posts/)에 있는 회사면, 새 정보가 나올 수 없는 경우 추출 전에 건너뛴다 (매일 실행 시 API 호출 절약)
            if not only_rcept:
                from gongsi.publish.store import all_posts
                prev = [p.meta for p in all_posts() if p.meta.get("corp") == d.corp_name]
                if any(m.get("phase") == "post" for m in prev):
                    log("skip", corp=d.corp_name, reason="수요예측 결과까지 이미 게시 대기열에 있음")
                    continue
                if any(m.get("phase") == "pre" for m in prev) and "발행조건확정" not in (d.report_name or ""):
                    log("skip", corp=d.corp_name, reason="수요예측 전 게시물이 이미 있고, 이번 공시는 발행조건확정이 아님")
                    continue

            # 4. 추출
            db.stage(run_id, "extract")
            facts = adapter.ipo_facts(d)
            log("extract", corp=d.corp_name, documents=facts.raw.get("documents"), found=sorted(facts.raw.get("found", {}).keys()),
                missing=[k for k in ("band_low_krw", "final_price_krw", "demand_ratio", "lockup_ratio_pct", "float_ratio_pct",
                                     "subscription_start", "refund_date", "listing_date", "revenue_krw") if getattr(facts, k) is None])
            if not only_rcept:
                if facts.subscription_end and facts.subscription_end < today.isoformat():
                    log("skip", corp=d.corp_name, reason=f"청약 종료 {facts.subscription_end}")
                    continue
                if facts.subscription_start and facts.subscription_start > (today + timedelta(days=settings.lead_days)).isoformat():
                    log("skip", corp=d.corp_name, reason=f"청약 {facts.subscription_start}, 아직 {settings.lead_days}일 이상 남음")
                    continue
                if not facts.subscription_start:
                    log("skip", corp=d.corp_name, reason="청약일을 원문에서 못 읽음")
                    continue
            summary, how = summarize(facts)
            log("summary", corp=d.corp_name, summary_by=how, lines=summary.business_lines, risks=summary.risk_lines)

            # 5. 검증
            db.stage(run_id, "verify")
            doc = cardmod.doc_kind(d.report_name)  # 출처 줄 문서 이름: 투자설명서 접수번호면 투자설명서로
            card_list, derived = cardmod.build(facts, summary, today, d.rcept_no, doc)
            texts = [t for c in card_list for t in c.texts() if t and t not in c.verbatim]
            allowed_derived = set(derived.keys())
            problems = [p for p in check_numbers(texts, facts) if not any(f"'{k}'" in p for k in allowed_derived)]
            problems += check_forbidden(texts) + check_summary(summary, facts) + check_risks(summary, facts)
            occ = int(facts.raw.get("found", {}).get("revenue_occurrences", "2") or 2)
            if facts.revenue_krw and occ < 2:
                problems.append(f"매출 숫자가 원문에 {occ}번만 나옴 (열 선택 오류 의심)")
            problems += check_forbidden(summary.business_lines + [facts.industry or ""] + [l for pair in summary.risk_lines for l in pair])
            # 캡션도 카드와 같은 기준으로: 숫자는 facts·derived에서만 (출처 줄의 접수번호는 제외), 금지어 없음, 줄바꿈 규칙
            cap = cardmod.caption(facts, d.rcept_no, summary, doc)
            cap_body = cap.replace(d.rcept_no, "")  # 접수번호는 공시 숫자가 아니라 출처 표기
            problems += [f"캡션: {p}" for p in check_numbers([cap_body], facts) if not any(f"'{k}'" in p for k in allowed_derived)]
            problems += [f"캡션: {p}" for p in check_forbidden([cap_body])]
            problems += [f"캡션: {p}" for p in check_caption_layout(cap)]
            verified = not problems
            db.add_fact(issue_ids[d.rcept_no], facts.model_dump(), {"summary": summary.model_dump(), "by": how, "derived": derived},
                        verified, "; ".join(problems) if problems else None)
            log("verify", corp=d.corp_name, ok=verified, problems=problems, derived=derived)
            if not verified:
                continue

            # 6. 카드 생성
            db.stage(run_id, "render")
            out = settings.out_dir / f"run{run_id}"
            logo_path, logo_how = fetch_logo(facts.corp_name, facts.homepage)
            log("logo", corp=d.corp_name, homepage=facts.homepage, result=logo_how)
            paths, image_log = render(card_list, facts.corp_name, facts.industry or "", out, d.rcept_no)
            (out / f"{d.rcept_no}_caption.txt").write_text(cap, encoding="utf-8")
            db.add_post(run_id, issue_ids[d.rcept_no], [c.__dict__ for c in card_list], [str(p) for p in paths], cap)
            if facts.listing_date:
                db.add_followup(db.conn.execute("SELECT id FROM disclosure WHERE rcept_no=?", (d.rcept_no,)).fetchone()["id"],
                                "listing_result", facts.listing_date)
            log("render", corp=d.corp_name, files=[p.name for p in paths], images=image_log, caption_file=f"{d.rcept_no}_caption.txt")
            # 게시 단계(python -m gongsi.publish stage)가 읽는 목록
            with (settings.out_dir / "produced.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps({"run": run_id, "rcept_no": d.rcept_no, "corp": d.corp_name, "date": today.isoformat(),
                                    "subscription_start": facts.subscription_start, "n": len(paths),
                                    "phase": "post" if facts.demand_ratio is not None else "pre",
                                    "sample": adapter.name == "sample"}, ensure_ascii=False) + "\n")
            produced += 1

        db.finish_run(run_id, "ok")
        return produced
    except Exception as e:
        db.finish_run(run_id, f"error:{type(e).__name__}")
        log("error", error=str(e))
        raise


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", action="store_true", help="픽스처로 실행 (키 불필요)")
    ap.add_argument("--date", help="YYYY-MM-DD, 기본 오늘")
    ap.add_argument("--days", type=int, default=1, help="date 기준 며칠 전까지")
    ap.add_argument("--rcept-no", help="특정 접수번호만")
    ap.add_argument("--today", help="D-day 계산 기준일 (테스트용)")
    args = ap.parse_args(argv)

    today = date.fromisoformat(args.today) if args.today else date.today()
    date_to = args.date or today.isoformat()
    date_from = (date.fromisoformat(date_to) - timedelta(days=args.days - 1)).isoformat()

    if args.sample:
        from gongsi.adapters.sample import SampleAdapter
        adapter: Adapter = SampleAdapter(settings.fixtures_dir)
    else:
        from gongsi.adapters.dart import DartAdapter
        adapter = DartAdapter(settings.dart_api_key)

    n = run(adapter, date_from, date_to, args.rcept_no, today)
    log("done", produced=n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
