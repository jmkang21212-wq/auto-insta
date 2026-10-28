"""게시 시간대 (2026-10-28 사용자 결정: 미국은 아침, 한국은 오후. 일정이 겹치면 둘 다 올리되 간격을 둔다).

승인은 아무 때나 받고, 실제 게시는 그 게시물에 정해 둔 시각(meta['publish_at'], 한국 시간) 이후에 한다.
같은 날 같은 시장 게시물이 여럿이면 다음 자리로 2시간씩 밀린다. 그날 자리가 다 지났으면 None = 승인 즉시 게시.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

KST = timezone(timedelta(hours=9))
SLOTS = {
    "US": ("08:00", "10:00", "12:00"),         # 미국 장 마감 뒤 아침 출근길
    "KR": ("13:00", "15:00", "17:00", "19:00"),  # 오후
}


def now_kst() -> datetime:
    return datetime.now(KST)


def parse(s: str | None) -> datetime | None:
    if not s:
        return None
    t = datetime.fromisoformat(s)
    return t if t.tzinfo else t.replace(tzinfo=KST)


def assign(market: str, day: date, taken: set[str], now: datetime | None = None) -> str | None:
    """market의 day 자리 가운데 아직 안 지났고 안 쓴 첫 자리 (ISO 문자열). 없으면 None"""
    now = now or now_kst()
    for hm in SLOTS.get(market, SLOTS["KR"]):
        h, m = map(int, hm.split(":"))
        t = datetime.combine(day, time(h, m), KST)
        if t <= now or t.isoformat() in taken:
            continue
        return t.isoformat()
    return None


def due(publish_at: str | None, now: datetime | None = None) -> bool:
    t = parse(publish_at)
    return t is None or t <= (now or now_kst())


def label(publish_at: str | None) -> str:
    """텔레그램 안내용: '오늘 13:00' / '10월 29일 13:00'"""
    t = parse(publish_at)
    if not t:
        return "바로"
    today = now_kst().date()
    return f"{'오늘' if t.date() == today else f'{t.month}월 {t.day}일'} {t:%H:%M}"
