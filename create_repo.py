# -*- coding: utf-8 -*-
"""读取本机存储的 GitHub 凭据，调用 GitHub API 创建仓库。"""
import json
import subprocess
import sys
import urllib.request


def get_github_token():
    proc = subprocess.run(
        ["git", "credential", "fill"],
        input="protocol=https\nhost=github.com\n",
        text=True,
        capture_output=True,
        encoding="utf-8",
    )
    username = None
    password = None
    for line in proc.stdout.splitlines():
        if line.startswith("username="):
            username = line.split("=", 1)[1]
        elif line.startswith("password="):
            password = line.split("=", 1)[1]
    if not username or not password:
        raise RuntimeError("未能从 git credential 读取 GitHub 凭据")
    return username, password


def create_repo(name, description, private=False):
    username, token = get_github_token()
    payload = json.dumps({
        "name": name,
        "description": description,
        "private": private,
        "auto_init": False,
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://api.github.com/user/repos",
        data=payload,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data["html_url"], data["clone_url"]


if __name__ == "__main__":
    url, clone = create_repo(
        name="chronos-alarm",
        description="Chronos 闹钟 —— 基于 pywebview 的 Windows 桌面闹钟，深蓝科技风，支持系统托盘常驻、中国调休工作日与自定义铃声。",
        private=False,
    )
    print(f"REPO_URL={url}")
    print(f"CLONE_URL={clone}")
