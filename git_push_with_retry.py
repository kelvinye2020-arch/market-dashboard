# -*- coding: utf-8 -*-
"""
git push 带超时重试 + 事后验证。
解决：公司网络到 github.com 的 TLS 握手偶发卡死，导致 push 卡 8 分钟失败、且被系统误判"成功"。
用法：python git_push_with_retry.py
退出码：0=推送成功且已生效；1=重试全部失败；2=推送未生效(本地仍 ahead>0)
依赖：主目录 remote 已配置为带 token 的 HTTPS URL（见 .git/config），本脚本不含任何密钥。
"""
import os
import re
import sys
import time
import subprocess

REPO = os.path.dirname(os.path.abspath(__file__))
BRANCH = "main"
MAX_RETRY = 3
TIMEOUT = 90  # 单次 push 超时（秒），TLS 握手卡死超过此时长会被杀掉重试


def run(cmd, timeout=TIMEOUT):
    try:
        r = subprocess.run(
            cmd, cwd=REPO, capture_output=True, text=True,
            timeout=timeout, encoding="utf-8", errors="replace",
        )
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except subprocess.TimeoutExpired:
        return -1, f"[TIMEOUT {timeout}s]"


def main():
    # 1. push，失败重试
    for i in range(1, MAX_RETRY + 1):
        print(f"[push] 第 {i}/{MAX_RETRY} 次 ...", flush=True)
        code, out = run(["git", "push", "origin", BRANCH])
        if code == 0:
            print("[push] 成功", flush=True)
            break
        print(f"[push] 失败 exit={code}: {out.strip()[-200:]}", flush=True)
        if i < MAX_RETRY:
            time.sleep(10)
    else:
        print("[push] ✘ 重试 3 次全部失败", flush=True)
        return 1

    # 2. 事后验证：本地必须不再领先 origin（ahead=0），否则说明推送没真正生效
    code, out = run(["git", "status", "-sb"], timeout=30)
    m = re.search(r"ahead (\d+)", out)
    ahead = int(m.group(1)) if m else 0
    if ahead > 0:
        print(f"[verify] ✘ 本地仍领先 origin {ahead} 个 commit，推送未生效", flush=True)
        return 2
    print("[verify] ✓ ahead=0，推送已生效", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
