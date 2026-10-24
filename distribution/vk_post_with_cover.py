#!/usr/bin/env python3
"""Публикация в VK с обложкой — одним вызовом.

Зачем отдельный скрипт: `wall.edit` с групповым токеном недоступен
(error 27), поэтому прикрепить картинку к уже опубликованному посту нельзя.
Единственный рабочий путь — прикреплять обложку в момент публикации.

Проверено: photos.getWallUploadServer → upload → saveWallPhoto → wall.post
с `attachments=photo-<group>_<id>` работает групповым токеном.

Использование:
    python3 vk_post_with_cover.py --group 239991204 --token-env VK_AGENTS_TOKEN \
        --cover assets/vk-cover.png --text-file post.txt [--publish]

Без `--publish` — только загрузка обложки и проверка, что photo_id получен.
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

# Секреты живут в ~/.env; shell-загрузка спотыкается на спецсимволах в других
# строках, поэтому читаем точечно и только нужное.
def _env_value(key: str) -> str:
    env_file = Path.home() / ".env"
    if not env_file.exists():
        return ""
    for line in env_file.read_text(encoding="utf-8", errors="ignore").splitlines():
        if line.startswith(key + "="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""

API = "https://api.vk.com/method/"


def call(method: str, token: str, **params) -> dict:
    params = {**params, "access_token": token, "v": "5.199"}
    url = API + method + "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=30) as r:
        data = json.loads(r.read().decode())
    if "error" in data:
        raise RuntimeError(f"{method}: {data['error'].get('error_msg')}")
    return data["response"]


def upload(url: str, path: Path) -> dict:
    """multipart/form-data без сторонних библиотек."""
    boundary = uuid.uuid4().hex
    ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    body = bytearray()
    body += f"--{boundary}\r\n".encode()
    body += f'Content-Disposition: form-data; name="photo"; filename="{path.name}"\r\n'.encode()
    body += f"Content-Type: {ctype}\r\n\r\n".encode()
    body += path.read_bytes()
    body += f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(url, data=bytes(body), headers={
        "Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--group", required=True, help="id группы без минуса")
    ap.add_argument("--token-env", default="VK_AGENTS_TOKEN")
    ap.add_argument("--cover", required=True)
    ap.add_argument("--text-file")
    ap.add_argument("--publish", action="store_true")
    args = ap.parse_args()

    token = os.getenv(args.token_env, "") or _env_value(args.token_env)
    if not token:
        return print(f"нет {args.token_env} в окружении", file=sys.stderr) or 2
    cover = Path(args.cover)
    if not cover.exists():
        return print(f"нет файла {cover}", file=sys.stderr) or 2

    # Групповому токену доступен только messages-загрузчик: wall-загрузчик
    # отвечает error 27. Фото, сохранённое через messages, прикрепляется к посту
    # тем же `photo-<group>_<id>`.
    try:
        server = call("photos.getWallUploadServer", token, group_id=args.group)
        uploader, saver = "wall", "saveWallPhoto"
    except RuntimeError:
        server = call("photos.getMessagesUploadServer", token,
                      group_id=args.group, peer_id=0)
        uploader, saver = "messages", "saveMessagesPhoto"
    uploaded = upload(server["upload_url"], cover)
    saved = call(f"photos.{saver}", token, group_id=args.group,
                 photo=uploaded["photo"], server=uploaded["server"],
                 hash=uploaded["hash"])
    print(f"  загрузчик: {uploader}")
    photo = saved[0]
    attachment = f"photo-{args.group}_{photo['id']}"
    print(f"  обложка загружена: {attachment}")

    if not args.publish:
        print("  публикация не запрошена (--publish не указан)")
        return 0
    if not args.text_file:
        return print("нужен --text-file", file=sys.stderr) or 2
    text = Path(args.text_file).read_text(encoding="utf-8")
    post = call("wall.post", token, owner_id=f"-{args.group}", from_group=1,
                message=text, attachments=attachment)
    print(f"  пост: https://vk.com/wall-{args.group}_{post['post_id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
