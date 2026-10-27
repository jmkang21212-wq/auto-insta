"""텔레그램 승인 봇. 서버 없이 getUpdates 폴링으로만 동작합니다.

준비: @BotFather 에서 봇 생성 → TELEGRAM_BOT_TOKEN. 봇에게 아무 말이나 보낸 뒤
  https://api.telegram.org/bot<TOKEN>/getUpdates 에서 chat.id 를 읽어 TELEGRAM_CHAT_ID.

보내는 것: 카드 전부(최대 10장 미디어 그룹) + 캡션 + [게시 승인] [넘기기] 버튼.
버튼 응답(callback_query)은 poll()이 읽고, 처리한 update_id는 state/telegram_offset.json 에 저장해 두 번 처리하지 않습니다.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import requests

from gongsi.publish.store import ROOT, Post

STATE = ROOT / "state" / "telegram_offset.json"


def _api(method: str, **kw) -> dict:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    files = kw.pop("files", None)
    r = requests.post(f"https://api.telegram.org/bot{token}/{method}", data=kw, files=files, timeout=60)
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"telegram {method}: {data.get('description')}")
    return data["result"]


def chat_id() -> str:
    return os.environ["TELEGRAM_CHAT_ID"]


def send_for_approval(post: Post) -> dict:
    """미디어 그룹 + 버튼 메시지. 돌려주는 dict를 meta['telegram']에 저장한다."""
    imgs = post.images[:10]
    media = []
    files = {}
    for i, p in enumerate(imgs):
        key = f"img{i}"
        files[key] = (p.name, p.read_bytes(), "image/jpeg")
        media.append({"type": "photo", "media": f"attach://{key}"})
    _api("sendMediaGroup", chat_id=chat_id(), media=json.dumps(media), files=files)

    head = f"[{post.meta['corp']}] {post.meta['n']}장 · {post.meta['date']}\n접수번호 {post.rcept_no}\n\n"
    text = (head + post.caption)[:3900]
    keyboard = {"inline_keyboard": [[
        {"text": "✅ 게시 승인", "callback_data": f"ok:{post.rcept_no}"},
        {"text": "⏭ 넘기기", "callback_data": f"no:{post.rcept_no}"},
    ]]}
    msg = _api("sendMessage", chat_id=chat_id(), text=text, reply_markup=json.dumps(keyboard), disable_web_page_preview="true")
    return {"message_id": msg["message_id"], "chat_id": msg["chat"]["id"]}


def notify(text: str) -> None:
    _api("sendMessage", chat_id=chat_id(), text=text[:3900], disable_web_page_preview="true")


def read_decisions() -> list[tuple[str, str, dict]]:
    """[(결정 'ok'|'no', 접수번호, callback_query)] 를 돌려주고 오프셋을 저장한다."""
    offset = 0
    if STATE.exists():
        offset = json.loads(STATE.read_text(encoding="utf-8")).get("offset", 0)
    updates = _api("getUpdates", offset=offset, timeout=0, allowed_updates=json.dumps(["callback_query"]))
    out = []
    last = offset
    for u in updates:
        last = max(last, u["update_id"] + 1)
        cq = u.get("callback_query")
        if not cq or ":" not in (cq.get("data") or ""):
            continue
        decision, rcept = cq["data"].split(":", 1)
        if decision in ("ok", "no"):
            out.append((decision, rcept, cq))
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps({"offset": last}), encoding="utf-8")
    return out


def ack(cq: dict, text: str, remove_buttons: bool = True) -> None:
    try:
        _api("answerCallbackQuery", callback_query_id=cq["id"], text=text[:200])
    except RuntimeError:
        pass  # 오래된 콜백은 answer가 거부될 수 있다
    if remove_buttons and cq.get("message"):
        try:
            _api("editMessageReplyMarkup", chat_id=cq["message"]["chat"]["id"], message_id=cq["message"]["message_id"],
                 reply_markup=json.dumps({"inline_keyboard": []}))
        except RuntimeError:
            pass


def recent_chats() -> list[dict]:
    """봇에게 온 최근 메시지의 대화방 목록 (TELEGRAM_CHAT_ID 찾기용). 오프셋을 건드리지 않아 승인 버튼 처리에 영향 없음."""
    ups = _api("getUpdates", timeout=0, allowed_updates=json.dumps(["message", "callback_query"]))
    seen = {}
    for u in ups:
        m = u.get("message") or (u.get("callback_query") or {}).get("message") or {}
        c = m.get("chat")
        if c:
            seen[c["id"]] = {"chat_id": c["id"], "type": c.get("type"), "name": c.get("title") or c.get("first_name") or c.get("username"),
                             "last_text": (m.get("text") or "")[:30]}
    return list(seen.values())


def bot_info() -> dict:
    return _api("getMe")
