from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    dart_api_key: str = os.getenv("DART_API_KEY", "")
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    out_dir: Path = ROOT / os.getenv("GONGSI_OUT_DIR", "out")
    db_path: Path = ROOT / os.getenv("GONGSI_DB_PATH", "out/gongsi.sqlite")
    templates_dir: Path = ROOT / "gongsi" / "templates"
    fixtures_dir: Path = ROOT / "fixtures"

    # 선정 규칙
    max_issues: int = 4
    min_issues: int = 1
    standalone_ratio: float = 2.0  # 1위 점수가 2위의 2배 이상이면 단독 편
    lead_days: int = 7  # 공모주: 청약 시작이 이 날수 안에 들어오면 카드 생성 (그보다 멀면 다음 날 다시)

    # 카드 규격 (인스타그램 캐러셀 세로 4:5)
    card_width: int = 1080
    card_height: int = 1350


settings = Settings()
