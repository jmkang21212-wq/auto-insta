"""유명 공모주와의 비교 한 줄.

"평균"이 아니라 "이름을 아는 회사는 이랬다"를 보여줍니다. 숫자는 fixtures/benchmarks.json 에서만 오고,
각 숫자의 출처 URL이 derived 로그에 같이 남습니다. 판단어(좋다·나쁘다)는 쓰지 않고 높다·낮다·많다·적다만 씁니다.
"""
from __future__ import annotations

import json
from pathlib import Path

from gongsi.config import settings


def _load() -> list[dict]:
    p = settings.fixtures_dir / "benchmarks.json"
    return json.loads(p.read_text(encoding="utf-8"))["items"] if p.exists() else []


def _fmt_ratio(v: float) -> str:
    return f"{v:,.0f}:1" if float(v).is_integer() else f"{v:,.2f}:1"


def compare_float(value: float | None, derived: dict[str, str]) -> str:
    """3장: 유통 가능 물량."""
    if value is None:
        return ""
    refs = [b for b in _load() if b.get("float_pct") is not None]
    if not refs:
        return ""
    b = refs[0]
    derived[f"{b['float_pct']:g}"] = f"benchmark {b['corp']} float_pct ({b['sources']['float_pct']})"
    rel = "많은" if value > b["float_pct"] else "적은"
    return f"{b['corp']} 상장 땐 {b['float_pct']:g}%였어요. 그보다 {rel} 편이에요"


def compare_ratio(derived: dict[str, str]) -> str:
    """4장: 경쟁률. 두 회사를 한 줄로."""
    refs = [b for b in _load() if b.get("demand_ratio") is not None][:2]
    parts = []
    for b in refs:
        derived[_fmt_ratio(b["demand_ratio"]).replace(":1", "")] = f"benchmark {b['corp']} demand_ratio ({b['sources']['demand_ratio']})"
        parts.append(f"{b['corp']} {_fmt_ratio(b['demand_ratio'])}")
    return ("유명 공모주는 " + ", ".join(parts) + "이었어요") if parts else ""


def compare_lockup(derived: dict[str, str]) -> str:
    """5장: 확약 비율."""
    refs = [b for b in _load() if b.get("lockup_pct") is not None][:2]
    parts = []
    for b in refs:
        derived[f"{b['lockup_pct']:g}"] = f"benchmark {b['corp']} lockup_pct ({b['sources']['lockup_pct']})"
        parts.append(f"{b['corp']} {b['lockup_pct']:g}%")
    return ("유명 공모주는 " + ", ".join(parts) + "였어요") if parts else ""


def compare_offering(amount: int | None, derived: dict[str, str]) -> str:
    """1장 보조: 공모 규모."""
    if amount is None:
        return ""
    refs = [b for b in _load() if b.get("offering_krw")]
    if not refs:
        return ""
    b = refs[0]
    eok = b["offering_krw"] // 100_000_000
    derived[f"{eok:,}"] = f"benchmark {b['corp']} offering_krw ({b['sources']['offering_krw']})"
    derived[str(eok)] = derived[f"{eok:,}"]
    rel = "큰" if amount > b["offering_krw"] else "작은"
    return f"{b['corp']}({eok:,}억)보다 {rel} 규모예요"


def refs(metric: str, derived: dict[str, str], limit: int = 2) -> list[dict]:
    """카드 비교 차트용 원시 목록. metric: float_pct / demand_ratio / lockup_pct. derived에 출처를 남긴다."""
    out = []
    for b in [b for b in _load() if b.get(metric) is not None][:limit]:
        v = b[metric]
        label = _fmt_ratio(v) if metric == "demand_ratio" else f"{v:g}%"
        key = _fmt_ratio(v).replace(":1", "") if metric == "demand_ratio" else f"{v:g}"
        derived[key] = f"benchmark {b['corp']} {metric} ({b['sources'][metric]})"
        out.append({"name": b["corp"], "value": float(v), "label": label, "when": b.get("listed", "")})
    return out
