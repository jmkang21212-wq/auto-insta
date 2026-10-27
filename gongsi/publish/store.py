"""posts/ 폴더 = 게시 대기열 + 이미지 호스팅.

posts/<YYYY-MM-DD>/<접수번호>/
  01.jpg … NN.jpg   카드 (PNG → JPEG. 인스타그램은 JPEG만 받고, 공개 URL에서 직접 가져간다)
  reel.mp4          훅 릴스 (있으면 릴스로 게시. 다른 훅으로 다시 만들면 reel_2.mp4 처럼 새 이름: raw 주소 캐시 회피)
  bundle.json       다시 만들기 재료 (facts, summary, 훅 후보, 고른 번호)
  caption.txt
  meta.json         {corp, rcept_no, date, n, status: pending|approved|rejected|published|failed, telegram: {...}, instagram: {...}}

공개 URL은 깃허브 raw 주소입니다: https://raw.githubusercontent.com/<owner>/<repo>/<branch>/posts/...
저장소가 public이어야 메타 서버가 이미지를 가져갈 수 있습니다.
"""
from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from PIL import Image

from gongsi.config import settings

ROOT = settings.templates_dir.parent.parent
POSTS = ROOT / "posts"


def repo_slug() -> str:
    """owner/repo. Actions에서는 GITHUB_REPOSITORY가 자동으로 있고, 로컬은 GONGSI_REPO로."""
    slug = os.getenv("GONGSI_REPO") or os.getenv("GITHUB_REPOSITORY") or ""
    if not slug:
        raise RuntimeError("GONGSI_REPO(owner/repo)가 비어 있습니다. 이미지 공개 URL을 만들 수 없어요")
    return slug


def raw_url(rel: Path) -> str:
    branch = os.getenv("POSTS_BRANCH", "main")
    return f"https://raw.githubusercontent.com/{repo_slug()}/{branch}/{rel.as_posix()}"


@dataclass
class Post:
    dir: Path
    meta: dict = field(default_factory=dict)

    @property
    def rcept_no(self) -> str:
        return self.meta["rcept_no"]

    @property
    def images(self) -> list[Path]:
        return sorted(self.dir.glob("[0-9][0-9].jpg"))

    @property
    def reel(self) -> Path | None:
        name = self.meta.get("reel")
        return self.dir / name if name and (self.dir / name).exists() else None

    @property
    def reel_url(self) -> str | None:
        return raw_url(self.reel.relative_to(ROOT)) if self.reel else None

    @property
    def caption(self) -> str:
        return (self.dir / "caption.txt").read_text(encoding="utf-8")

    @property
    def urls(self) -> list[str]:
        return [raw_url(p.relative_to(ROOT)) for p in self.images]

    def save(self) -> None:
        (self.dir / "meta.json").write_text(json.dumps(self.meta, ensure_ascii=False, indent=1), encoding="utf-8")


def stage(run_dir: Path, rcept_no: str, corp: str, when: date | None = None) -> Post:
    """out/run<N>/<접수번호>_NN.png + _caption.txt → posts/<날짜>/<접수번호>/ (JPEG). 이미 있으면 덮어쓴다."""
    when = when or date.today()
    out = POSTS / when.isoformat() / rcept_no
    out.mkdir(parents=True, exist_ok=True)
    pngs = sorted(run_dir.glob(f"{rcept_no}_[0-9][0-9].png"))
    if not pngs:
        raise FileNotFoundError(f"{run_dir}에 {rcept_no} 카드가 없습니다")
    for old in out.glob("*.jpg"):
        old.unlink()
    for i, png in enumerate(pngs, start=1):
        Image.open(png).convert("RGB").save(out / f"{i:02d}.jpg", "JPEG", quality=90, optimize=True)
    shutil.copy(run_dir / f"{rcept_no}_caption.txt", out / "caption.txt")
    reel = run_dir / f"{rcept_no}_reel.mp4"
    if reel.exists():
        shutil.copy(reel, out / "reel.mp4")
    if (run_dir / f"{rcept_no}_bundle.json").exists():
        shutil.copy(run_dir / f"{rcept_no}_bundle.json", out / "bundle.json")
    post = Post(out, {"corp": corp, "rcept_no": rcept_no, "date": when.isoformat(), "n": len(pngs),
                      "status": "pending", "source_run": run_dir.name,
                      "format": "reel" if reel.exists() else "carousel", "reel": "reel.mp4" if reel.exists() else None})
    post.save()
    return post


def load(dir: Path) -> Post:
    return Post(dir, json.loads((dir / "meta.json").read_text(encoding="utf-8")))


def all_posts(status: str | None = None) -> list[Post]:
    out = []
    for meta in sorted(POSTS.glob("*/*/meta.json")):
        p = load(meta.parent)
        if status is None or p.meta.get("status") == status:
            out.append(p)
    return out


def find(rcept_no: str) -> Post | None:
    hits = [p for p in all_posts() if p.rcept_no == rcept_no]
    return hits[-1] if hits else None
