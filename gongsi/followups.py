"""후속편: 공시가 새로 안 나와도 날짜가 되면 올리는 게시물 (2026-10-28 '공모주 여러 번 다루기').

확약 해제편 (lockup<N>): 상장 뒤 묶여 있던 주식(최대주주·벤처금융 등 의무보유)이 풀리기 전 마지막 평일 오후.
  근거: 증권신고서 '[상장 후 유통가능 주식수 현황]' 누적표 (facts.float_schedule). 신고서 주석대로 공모 주주(기관)의
  의무보유 확약 물량은 빠진 숫자라 캡션에 그 말을 꼭 붙인다. 풀리는 날 = 상장일 + N개월 (달력 기준, 공휴일은 안 셈).
  릴스만 (카드뉴스 없음: 숫자 서너 개라 캡션으로 충분)
"""
from __future__ import annotations

import calendar
import json
from datetime import date, timedelta

from gongsi import cards as cardmod
from gongsi.config import settings
from gongsi.hook import Hook, phase_title
from gongsi.models import IpoFacts
from gongsi.reel import make_reel
from gongsi.verify import check_caption_layout, check_forbidden, check_numbers


def log(stage: str, **kw):
    print(json.dumps({"stage": stage, **kw}, ensure_ascii=False), flush=True)


def add_months(d: date, n: int) -> date:
    y, m = divmod(d.month - 1 + n, 12)
    y, m = d.year + y, m + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def next_business_day(d: date) -> date:
    d = d + timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def releases(facts: IpoFacts) -> list[dict]:
    """누적표에서 주식이 실제로 늘어나는 시점만: [{months, date, before, after, added}]"""
    sched = sorted(facts.float_schedule or [], key=lambda r: r["months"])
    if not facts.listing_date or len(sched) < 2:
        return []
    listed = date.fromisoformat(facts.listing_date)
    out = []
    for prev, cur in zip(sched, sched[1:]):
        added = (cur.get("shares") or 0) - (prev.get("shares") or 0)
        if cur["months"] > 0 and added > 0:
            out.append({"months": cur["months"], "date": add_months(listed, cur["months"]), "before": prev, "after": cur, "added": added})
    return out


def due_today(rel: dict, today: date) -> bool:
    """풀리기 전 마지막 평일: 오늘 < 풀리는 날 <= 다음 평일"""
    return today < rel["date"] <= next_business_day(today)


def lockup_caption(facts: IpoFacts, src: str, doc: str, hook: Hook | None, rel: dict, credit: str = "") -> str:
    corp = facts.corp_name

    def item(sign: str, text: str) -> list[str]:
        return cardmod.wrap(f"{sign} {text}")

    first = cardmod.wrap(hook.headline()) if hook else [f"🔓 {corp}, 묶인 주식 풀림"]
    first += item("-", f"주인공은 {corp}, {cardmod.nb(cardmod.kdate(facts.listing_date))} {facts.market or ''} 상장".replace("  ", " "))
    key = item("-", f"상장 {rel['months']}개월이 지나는 {cardmod.nb(cardmod.kdate(rel['date'].isoformat()))}부터 묶여 있던 주식 일부를 팔 수 있게 됨")
    key += cardmod.wrap(": 상장 뒤 일정 기간 못 팔게 묶어 두는 걸 의무보유(보호예수)라고 함")
    key += item("-", f"유통 가능 주식은 {rel['before']['pct']:g}% → {rel['after']['pct']:g}%")
    key += item("→", f"새로 풀리는 주식은 {rel['added']:,}주 (신고서 누적표 차이)")
    key += cardmod.wrap(": 신고서 기준이라 기관이 따로 약속한 확약 물량은 빠진 숫자임")
    return cardmod._join([first, key] + cardmod._tail(facts, src, doc, credit, True, extra_tag="#보호예수"))


def lockup_posts(run_id: int, today: date) -> int:
    """posts/ 의 같은 회사 최신 게시물 재료(bundle.json)로 오늘 올릴 확약 해제편을 만든다. 만든 개수"""
    from gongsi.publish.store import all_posts

    posts = all_posts()
    latest: dict[str, tuple] = {}
    for p in posts:
        f = p.dir / "bundle.json"
        if not f.exists() or p.meta.get("status") == "rejected" or p.meta.get("phase", "").startswith("lockup"):
            continue
        b = json.loads(f.read_text(encoding="utf-8"))
        facts = IpoFacts(**b["facts"])
        if facts.listing_date and facts.float_schedule:
            latest[facts.corp_name] = (p, b, facts)  # all_posts는 날짜순이라 마지막이 최신
    out_dir = settings.out_dir / f"run{run_id}"
    out_dir.mkdir(parents=True, exist_ok=True)
    made = 0
    for corp, (p, b, facts) in latest.items():
        for rel in releases(facts):
            phase = f"lockup{rel['months']}"
            if not due_today(rel, today):
                continue
            if any(q.meta.get("corp") == corp and q.meta.get("phase") == phase for q in posts):
                log("lockup", corp=corp, phase=phase, skipped="이미 있음")
                continue
            src = b.get("source_rcept_no") or p.rcept_no.split("_")[0]
            key = f"{src}_{phase}"
            raw = b["hooks"][min(int(b.get("chosen") or 0), len(b["hooks"]) - 1)] if b.get("hooks") else None
            hook = Hook(**{k: raw[k] for k in ("title", "term", "emoji", "sub", "video", "explain", "alt_videos", "use") if k in raw}) if raw else None
            second = f"유통 가능 주식 {rel['before']['pct']:g}% → {rel['after']['pct']:g}%"
            video = None
            if hook:
                hook.title = phase_title(hook.title, phase)
                for q in hook.queries():
                    try:
                        video = make_reel(hook.title, f"({second})", hook.sub, q, out_dir / f"{key}_reel.mp4",
                                          thing=None if q == hook.video else "", use=hook.use)
                        break
                    except Exception as e:  # noqa: BLE001
                        log("lockup", corp=corp, query=q, ok=False, error=str(e)[:200])
            if not video:
                log("lockup", corp=corp, phase=phase, ok=False, error="릴스 영상을 못 만듦")
                continue
            doc = b.get("doc", "증권신고서")
            cap = lockup_caption(facts, src, doc, hook, rel, credit="📷 자료 화면: Pixabay")
            import re
            body = re.sub(r"\d{14}", "", cap)
            problems = [x for x in check_numbers([body, second], facts) if f"'{rel['added']:,}'" not in x]
            problems += check_forbidden([cap]) + check_caption_layout(cap)
            if problems:
                log("lockup", corp=corp, phase=phase, ok=False, problems=problems)
                continue
            (out_dir / f"{key}_caption.txt").write_text(cap, encoding="utf-8")
            (out_dir / f"{key}_bundle.json").write_text(json.dumps({
                "facts": facts.model_dump(exclude={"raw"}), "summary": b.get("summary"), "doc": doc,
                "hooks": [hook.__dict__] if hook else [], "chosen": 0, "line2": second, "video": video,
                "phase": phase, "source_rcept_no": src, "release": {**rel, "date": rel["date"].isoformat()}}, ensure_ascii=False), encoding="utf-8")
            with (settings.out_dir / "produced.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps({"run": run_id, "rcept_no": key, "corp": corp, "date": today.isoformat(), "n": 0,
                                    "phase": phase, "format": "reel", "market": "KR"}, ensure_ascii=False) + "\n")
            log("lockup", corp=corp, phase=phase, ok=True, release=rel["date"].isoformat(), added=rel["added"])
            made += 1
    return made
