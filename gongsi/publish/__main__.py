"""python -m gongsi.publish <명령>

  stage          최근 파이프라인 실행(out/produced.jsonl)의 카드를 posts/<날짜>/<접수번호>/ 로 옮긴다 (PNG→JPEG, meta.json pending)
  notify         pending 인데 아직 텔레그램에 안 보낸 게시물을 보내 승인을 받는다 (APPROVAL=auto 면 바로 게시)
  poll           텔레그램 버튼 응답을 읽어 승인된 것을 인스타그램에 게시한다 (approved 상태로 남은 것도 재시도)
                 릴스면 [N번 훅으로] 버튼에 그 훅으로 릴스·캡션을 다시 만들어 다시 승인 요청
  watch --minutes M   M분 동안 1분마다 poll (생성 실행이 끝난 뒤 그 자리에서 승인을 기다려 바로 게시. 대기 중인 게 없으면 바로 끝)
  publish --rcept-no N   승인 없이 바로 게시 (수동)
  check          인스타그램 토큰·한도, 텔레그램 봇 연결 확인
  refresh-token  인스타그램 장기 토큰 연장 (새 토큰을 출력. Actions에선 gh secret set 으로 저장)
  list           posts/ 상태 한눈에
  chat-id        봇에게 말 건 대화방의 chat_id 출력 (TELEGRAM_CHAT_ID 찾기)

종료 코드는 항상 0 (Actions가 빨갛게 되지 않게). 결과는 JSON 로그 한 줄씩.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import traceback
from datetime import date
from pathlib import Path

from gongsi.config import settings
from gongsi.publish import instagram, slots, store, telegram


def log(stage: str, **kw):
    print(json.dumps({"stage": stage, **kw}, ensure_ascii=False), flush=True)


def _publish(post: store.Post, how: str) -> bool:
    """format: both = 캐러셀(자세한 캡션) 먼저, 이어서 릴스(짧은 캡션). 캐러셀이 이미 올라갔으면 릴스만 (재시도 때 중복 방지).
    reel = 릴스만(2026-10-28 이전 게시물), carousel = 카드만"""
    fmt = post.meta.get("format")
    try:
        if fmt == "both" and post.reel and post.images and not post.meta.get("instagram_carousel", {}).get("media_id"):
            c = instagram.publish_carousel(post.urls, post.caption)
            post.meta["instagram_carousel"] = {**c, "published_at": date.today().isoformat(), "by": how}
            post.save()
            log("publish", corp=post.meta["corp"], rcept_no=post.rcept_no, part="carousel", ok=True, permalink=c.get("permalink"))
        if fmt in ("both", "reel") and post.reel:
            res = instagram.publish_reel(post.reel_url, post.caption_reel if fmt == "both" else post.caption,
                                         music=os.getenv("REEL_MUSIC", "1") != "0")
        else:
            res = instagram.publish_carousel(post.urls, post.caption)
    except Exception as e:
        # 3번까지는 approved로 남겨 다음 확인 때 다시 (둘 다 게시에서 캐러셀만 올라간 경우 릴스만 다시 올라감)
        attempts = post.meta.get("instagram", {}).get("attempts", 0) + 1
        post.meta["status"] = "approved" if attempts < 3 else "failed"
        post.meta["instagram"] = {"error": str(e)[:500], "attempts": attempts}
        post.save()
        log("publish", corp=post.meta["corp"], rcept_no=post.rcept_no, ok=False, error=str(e)[:300])
        _safe_notify(f"❌ {post.meta['corp']} 게시 실패\n{str(e)[:300]}")
        return False
    post.meta["status"] = "published"
    post.meta["instagram"] = {**res, "published_at": date.today().isoformat(), "by": how}
    post.save()
    log("publish", corp=post.meta["corp"], rcept_no=post.rcept_no, ok=True, permalink=res.get("permalink"), music=res.get("audio_id"))
    links = [post.meta.get("instagram_carousel", {}).get("permalink"), res.get("permalink") or res.get("media_id")]
    if res.get("audio_id"):
        note = f"\n🎵 {res.get('audio_title') or ''} - {res.get('audio_artist') or ''}".rstrip(" -")
    elif not post.reel:
        note = ""
    elif not instagram.fb_ready():
        note = "\n(음악 없이 올라감: 페이스북 로그인 토큰이 아직 없음)"
    else:
        note = f"\n(음악 붙이기 실패해서 소리 없이 올라감: {(res.get('music_error') or '')[:150]})"
    _safe_notify(f"✅ {post.meta['corp']} 게시됨\n" + "\n".join(l for l in links if l) + note)
    return True


def _safe_notify(text: str) -> None:
    if os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID"):
        try:
            telegram.notify(text)
        except Exception as e:
            log("telegram", ok=False, error=str(e)[:200])


def cmd_stage(args) -> None:
    produced = settings.out_dir / "produced.jsonl"
    if not produced.exists():
        log("stage", count=0, note="out/produced.jsonl 없음 (파이프라인이 카드를 만들지 않았음)")
        return
    rows = [json.loads(l) for l in produced.read_text(encoding="utf-8").splitlines() if l.strip()]
    run = args.run or max(r["run"] for r in rows)
    n = 0
    for r in rows:
        if r["run"] != run:
            continue
        if r.get("sample"):
            log("stage", corp=r["corp"], rcept_no=r["rcept_no"], skipped="픽스처(가상 회사)는 게시 대기열에 넣지 않음")
            continue
        if store.find(r["rcept_no"]) and not args.force:
            log("stage", corp=r["corp"], rcept_no=r["rcept_no"], skipped="이미 posts/에 있음")
            continue
        same = [p for p in store.all_posts() if p.meta.get("corp") == r["corp"] and p.meta.get("phase") == r.get("phase")
                and p.meta.get("status") != "rejected"]
        if same and not args.force:
            # 같은 회사의 같은 단계(수요예측 전 pre / 후 post)는 한 번만. 정정 공시가 여러 번 나와도 같은 내용을 두 번 올리지 않는다
            log("stage", corp=r["corp"], rcept_no=r["rcept_no"], skipped=f"같은 단계({r.get('phase')}) 게시물이 이미 있음: {same[-1].rcept_no}")
            continue
        post = store.stage(settings.out_dir / f"run{run}", r["rcept_no"], r["corp"])
        post.meta["phase"] = r.get("phase")
        post.meta["subscription_start"] = r.get("subscription_start")
        # 게시 시각: 미국 아침, 한국 오후. 같은 날 같은 시장이 겹치면 다음 자리(2시간 뒤)로 (2026-10-28)
        market = r.get("market") or "KR"
        taken = {p.meta.get("publish_at") for p in store.all_posts() if p.meta.get("publish_at") and p.meta.get("status") != "rejected"}
        post.meta["market"] = market
        post.meta["publish_at"] = slots.assign(market, date.today(), taken)
        post.save()
        _mark_video(post)
        log("stage", corp=r["corp"], rcept_no=r["rcept_no"], dir=str(post.dir.relative_to(store.ROOT)), n=post.meta["n"])
        n += 1
    log("stage", count=n, run=run)


def cmd_notify(args) -> None:
    auto = os.getenv("APPROVAL", "telegram").lower() == "auto"
    for post in store.all_posts("pending"):
        if auto:
            _publish(post, "auto")
            continue
        if post.meta.get("telegram") or post.meta.get("telegram_failures", 0) >= 2:
            continue  # 두 번 실패하면 멈춤: 카드 묶음만 가고 영상에서 실패하면 감시가 1분마다 같은 사진을 또 보내게 되므로
        try:
            post.meta["telegram"] = telegram.send_for_approval(post)
            post.save()
            log("notify", corp=post.meta["corp"], rcept_no=post.rcept_no, ok=True)
        except Exception as e:
            post.meta["telegram_failures"] = post.meta.get("telegram_failures", 0) + 1
            post.save()
            log("notify", corp=post.meta["corp"], rcept_no=post.rcept_no, ok=False, error=str(e)[:300])
            if post.meta["telegram_failures"] >= 2:
                _safe_notify(f"⚠️ {post.meta['corp']} 승인 요청을 두 번 못 보냈어요\n{str(e)[:200]}")


def cmd_poll(args) -> None:
    # 아침 생성 때 텔레그램 설정이 없었거나 전송이 실패해 아직 승인 요청이 안 간 게시물이 있으면 먼저 보낸다
    if os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID"):
        cmd_notify(args)
    else:
        log("poll", skipped="TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 없음")
        return
    decisions = telegram.read_decisions()
    log("poll", decisions=len(decisions))
    for decision, rcept, cq in decisions:
        post = store.find(rcept)
        if not post:
            telegram.ack(cq, "해당 게시물을 못 찾았어요")
            continue
        if post.meta["status"] in ("published",):
            telegram.ack(cq, "이미 게시된 글이에요")
            continue
        if decision.startswith("h"):  # 다른 훅으로 릴스·캡션을 다시 만들고 다시 승인 요청
            telegram.ack(cq, f"{decision[1:]}번 훅으로 다시 만드는 중…")
            try:
                _rerender(post, int(decision[1:]) - 1)
                _git_sync(f"posts: rerender {post.rcept_no} hook {decision[1:]}")
                post.meta["telegram"] = telegram.send_for_approval(post)
                post.save()
                log("poll", corp=post.meta["corp"], rcept_no=rcept, decision=decision, reel=post.meta.get("reel"))
            except Exception as e:  # noqa: BLE001
                log("poll", corp=post.meta["corp"], rcept_no=rcept, decision=decision, ok=False, error=str(e)[:300])
                _safe_notify(f"⚠️ {post.meta['corp']} {decision[1:]}번 훅으로 다시 만들기 실패\n{str(e)[:300]}")
            continue
        if decision == "no":
            post.meta["status"] = "rejected"
            post.save()
            telegram.ack(cq, "넘겼어요")
            log("poll", corp=post.meta["corp"], rcept_no=rcept, decision="rejected")
            continue
        post.meta["status"] = "approved"
        post.save()
        if not slots.due(post.meta.get("publish_at")):  # 정해 둔 시간대(미국 아침·한국 오후)까지 기다렸다가 게시
            telegram.ack(cq, f"승인됨, {slots.label(post.meta.get('publish_at'))}에 올려요")
            log("poll", corp=post.meta["corp"], rcept_no=rcept, decision="approved", publish_at=post.meta.get("publish_at"))
            continue
        telegram.ack(cq, "게시 중…")
        _publish(post, "telegram")
    # 승인된 것 가운데 시간이 된 것 게시 (+ 게시에 실패해 approved 로 남은 것 재시도, 최대 3회)
    for post in store.all_posts("approved"):
        if slots.due(post.meta.get("publish_at")) and post.meta.get("instagram", {}).get("attempts", 0) < 3:
            _publish(post, "slot" if not post.meta.get("instagram") else "retry")


def _mark_video(post: store.Post) -> None:
    """게시 대기열에 오른 릴스의 영상은 다음 릴스에서 다시 안 쓰게 state/used_media.json에 남긴다"""
    f = post.dir / "bundle.json"
    if post.meta.get("format") not in ("reel", "both") or not f.exists():
        return
    vid = (json.loads(f.read_text(encoding="utf-8")).get("video") or {}).get("id")
    if vid:
        from gongsi.reel import mark_used
        mark_used(vid)


def _rerender(post: store.Post, idx: int) -> None:
    """bundle.json의 idx번 훅으로 릴스와 캡션을 다시 만든다. 새 파일 이름(reel_N.mp4)으로 저장해 raw 주소 캐시를 피한다."""
    from gongsi import cards
    from gongsi.hook import Hook
    from gongsi.models import IpoFacts, Summary
    from gongsi.reel import make_reel, mark_used

    path = post.dir / "bundle.json"
    b = json.loads(path.read_text(encoding="utf-8"))
    hook = Hook(**b["hooks"][idx])
    name = f"reel_{idx + 1}.mp4"
    video, err = None, None
    for q in hook.queries():  # 첫 검색어로 영상이 없으면 더 흔한 장면 검색어로
        try:
            video = make_reel(hook.title, f"({b['line2']})" if b.get("line2") else "", hook.sub, q, post.dir / name,
                              thing=None if q == hook.video else "", use=hook.use)
            break
        except Exception as e:  # noqa: BLE001
            err = e
    if video is None:
        raise err or RuntimeError("영상을 못 찾음")
    mark_used(video["id"])
    facts, summary = IpoFacts(**b["facts"]), Summary(**b["summary"])
    src = b.get("source_rcept_no") or post.rcept_no  # 후속편(상장 전날·확약 해제)은 게시물 이름에 꼬리가 붙어 있다
    cap = cards.caption(facts, src, summary, b.get("doc", "증권신고서"), hook, credit="📷 자료 화면: Pixabay")
    (post.dir / "caption.txt").write_text(cap, encoding="utf-8")
    if post.meta.get("format") == "both":  # 캐러셀(자세한 판) + 릴스(짧은 판) 둘 다 새 훅으로
        (post.dir / "caption_reel.txt").write_text(
            cards.reel_caption(facts, src, summary, b.get("doc", "증권신고서"), hook, credit="📷 자료 화면: Pixabay"), encoding="utf-8")
    b.update(chosen=idx, video=video)
    path.write_text(json.dumps(b, ensure_ascii=False), encoding="utf-8")
    post.meta.update(reel=name, status="pending")
    post.save()


def _git_sync(msg: str) -> None:
    """Actions 안에서만(GIT_SYNC=1): 다시 만든 릴스를 바로 푸시해야 인스타가 raw 주소로 가져갈 수 있다"""
    if os.getenv("GIT_SYNC") != "1":
        return
    root = str(store.ROOT)
    subprocess.run(["git", "add", "posts", "state"], cwd=root, check=False)
    if subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=root).returncode != 0:
        subprocess.run(["git", "commit", "-q", "-m", msg], cwd=root, check=False)
        subprocess.run(["git", "pull", "-q", "--rebase", "origin", os.getenv("GITHUB_REF_NAME", "main")], cwd=root, check=False)
        subprocess.run(["git", "push", "-q"], cwd=root, check=False)


def cmd_watch(args) -> None:
    """생성 실행 끝에 승인을 그 자리에서 기다린다 (깃허브 예약 실행이 안 돌아서 30분 poll 대신, 2026-10-27).
    1분마다 poll. 승인·넘기기로 대기 중인 게 없어지면 끝, 최대 args.minutes분."""
    deadline = time.time() + args.minutes * 60
    while True:
        cmd_poll(args)
        _git_sync(f"posts: watch {time.strftime('%F %H:%M')}")
        waiting = [p for p in store.all_posts() if p.meta.get("status") in ("pending", "approved")]
        if not waiting or time.time() > deadline:
            log("watch", waiting=[p.rcept_no for p in waiting], done=not waiting)
            if waiting:
                _chain(waiting)
            return
        time.sleep(60)


def _chain(waiting: list[store.Post]) -> None:
    """감시 실행이 끝나는데 아직 할 일이 남았으면(승인돼 오후 시간대를 기다리는 것, 하루 안 된 승인 대기) 다음 감시 실행을 건다.
    깃허브 예약 실행은 몇 시간씩 늦거나 안 돌아서 이어 붙인다 (2026-10-28). 같은 줄(posts)에 대기 중인 실행이 이미 있으면
    새로 걸면 그걸 취소시키므로 걸지 않는다 (아침 생성 실행이 취소된 일이 있었음)"""
    repo, token = os.getenv("GITHUB_REPOSITORY"), os.getenv("GITHUB_TOKEN")
    if os.getenv("GITHUB_ACTIONS") != "true" or not (repo and token):
        return
    now = slots.now_kst()
    def worth(p: store.Post) -> bool:
        if p.meta.get("status") == "approved":
            return True
        sent = (p.meta.get("telegram") or {}).get("sent_at")
        return not sent or (now - slots.parse(sent)).total_seconds() < 20 * 3600
    if not any(worth(p) for p in waiting):
        log("chain", skipped="남은 건 하루 넘게 승인 안 된 것뿐")
        return
    import requests
    h = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    try:
        busy = [r for s in ("queued", "pending", "waiting", "requested")
                for r in requests.get(f"https://api.github.com/repos/{repo}/actions/runs", headers=h,
                                      params={"status": s, "per_page": 5}, timeout=30).json().get("workflow_runs", [])]
        if busy:
            log("chain", skipped=f"대기 중인 실행이 이미 있음: {[r.get('name') for r in busy]}")
            return
        r = requests.post(f"https://api.github.com/repos/{repo}/actions/workflows/publish.yml/dispatches", headers=h,
                          json={"ref": os.getenv("GITHUB_REF_NAME", "main"), "inputs": {"cmd": "watch", "minutes": "330"}}, timeout=30)
        log("chain", ok=r.status_code == 204, status=r.status_code, waiting=[p.rcept_no for p in waiting])
    except Exception as e:  # noqa: BLE001
        log("chain", ok=False, error=str(e)[:200])


def cmd_publish(args) -> None:
    post = store.find(args.rcept_no)
    if not post:
        log("publish", ok=False, error=f"posts/에 {args.rcept_no} 없음")
        return
    _publish(post, "manual")


def cmd_check(args) -> None:
    if os.getenv("IG_ACCESS_TOKEN"):
        try:
            me = instagram.whoami()
            log("check", instagram=me)
            log("check", limit=instagram.publishing_limit())
        except Exception as e:
            log("check", instagram_error=str(e)[:300])
    else:
        log("check", instagram="IG_ACCESS_TOKEN 없음")
    if instagram.fb_ready():  # 릴스 음악: 페이스북 로그인 토큰 → 연결된 인스타 계정 → 음악 목록 (2026-10-28)
        try:
            pages = instagram.fb_pages()
            log("check", fb_pages=[{"name": p.get("name"), "ig": (p.get("instagram_business_account") or {}).get("username")} for p in pages])
            ig = instagram.fb_ig_user_id()
            track = instagram.pick_music()
            log("check", music="ok", ig_fb_user_id=ig, sample=f"{track.get('title')} - {track.get('display_artist')}")
            _safe_notify(f"🎵 음악 연결 확인 ✅ 예: {track.get('title')} - {track.get('display_artist')}")
        except Exception as e:
            log("check", music_error=str(e)[:300])
            _safe_notify(f"⚠️ 음악 연결 확인 실패\n{str(e)[:300]}")
    else:
        log("check", music="FB_USER_TOKEN 없음")
    if os.getenv("TELEGRAM_BOT_TOKEN"):
        try:
            log("check", telegram_bot=telegram.bot_info().get("username"))
            if os.getenv("TELEGRAM_CHAT_ID"):
                telegram.notify("gongsi-cards 연결 확인 ✅")
                log("check", telegram="ok")
            else:
                log("check", telegram="TELEGRAM_CHAT_ID 없음. 봇에게 아무 말이나 보낸 뒤 chat-id 명령을 실행하세요")
        except Exception as e:
            log("check", telegram_error=str(e)[:300])
    else:
        log("check", telegram="TELEGRAM_BOT_TOKEN 없음")
    try:
        log("check", repo=store.repo_slug(), sample_url=store.raw_url(Path("posts/example/01.jpg")))
    except Exception as e:
        log("check", repo_error=str(e))


def cmd_refresh(args) -> None:
    data = instagram.refresh_token()
    days = int(data.get("expires_in", 0)) // 86400
    # 연장하면 같은 토큰 문자열의 기한만 늘어나는지, 새 문자열이 오는지 문서에 없음(확인 필요) → 바뀐 경우에만 Secret을 고친다
    changed = data["access_token"] != os.getenv("IG_ACCESS_TOKEN")
    log("refresh-token", expires_in_days=days, changed=changed)
    out = Path(os.getenv("GITHUB_OUTPUT", "")) if os.getenv("GITHUB_OUTPUT") else None
    if out:
        print(f"::add-mask::{data['access_token']}")  # 로그에 찍히지 않게
        with out.open("a", encoding="utf-8") as f:
            f.write(f"changed={'true' if changed else 'false'}\ndays={days}\n")
            if changed:
                f.write(f"token={data['access_token']}\n")
    elif changed:
        print(data["access_token"])


def cmd_chat_id(args) -> None:
    bot = telegram.bot_info()
    chats = telegram.recent_chats()
    log("chat-id", bot=f"@{bot.get('username')} ({bot.get('first_name')})", chats=chats,
        note=None if chats else f"@{bot.get('username')} 대화방에서 '시작'을 누르고 아무 말이나 보낸 뒤 다시 실행하세요")


def cmd_list(args) -> None:
    for p in store.all_posts():
        ig = p.meta.get("instagram", {})
        log("list", date=p.meta["date"], corp=p.meta["corp"], rcept_no=p.rcept_no, status=p.meta["status"],
            permalink=ig.get("permalink"), error=ig.get("error"))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("stage"); s.add_argument("--run", type=int); s.add_argument("--force", action="store_true")
    sub.add_parser("notify")
    sub.add_parser("poll")
    w = sub.add_parser("watch"); w.add_argument("--minutes", type=int, default=330)
    p = sub.add_parser("publish"); p.add_argument("--rcept-no", required=True)
    sub.add_parser("check")
    sub.add_parser("refresh-token")
    sub.add_parser("list")
    sub.add_parser("chat-id")
    args = ap.parse_args(argv)
    try:
        {"stage": cmd_stage, "notify": cmd_notify, "poll": cmd_poll, "watch": cmd_watch, "publish": cmd_publish,
         "check": cmd_check, "refresh-token": cmd_refresh, "list": cmd_list, "chat-id": cmd_chat_id}[args.cmd](args)
    except Exception as e:
        log("error", cmd=args.cmd, error=str(e)[:500])
        traceback.print_exc()
        _safe_notify(f"⚠️ gongsi-cards {args.cmd} 오류\n{str(e)[:300]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
