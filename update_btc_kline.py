# -*- coding: utf-8 -*-
"""
BTC K线数据更新脚本（v2 - Binance klines 数据源）
- 数据源：Binance api/v3/klines（日K，180天，免费稳定，无 token）
- 目标文件：btc_kline.json
- 兜底：失败 -> 保留原文件不动（避免坏数据覆盖好数据）
- 用法：python update_btc_kline.py [--push]
"""
import os
import sys
import json
import time
import datetime
import urllib.request
import urllib.error

# 强制 stdout 用 UTF-8，避免 Windows 控制台 GBK 报错
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TARGET_FILE = os.path.join(SCRIPT_DIR, "btc_kline.json")
# Binance 多镜像：主域名国内常被墙，data-api 子域可用
BINANCE_URLS = [
    "https://data-api.binance.vision/api/v3/klines?symbol=BTCUSDT&interval=1d&limit=180",
    "https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1d&limit=180",
    "https://api1.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1d&limit=180",
    "https://api3.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1d&limit=180",
]
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}


def log(msg):
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def fetch_binance(max_retry=3):
    """轮询多个 Binance 镜像，每个镜像最多重试 max_retry 次"""
    last_err = None
    for url in BINANCE_URLS:
        log(f"尝试镜像：{url.split('/api/')[0]}")
        for i in range(max_retry):
            try:
                req = urllib.request.Request(url, headers=HEADERS)
                raw = urllib.request.urlopen(req, timeout=25).read()
                data = json.loads(raw)
                if not isinstance(data, list) or len(data) < 30:
                    raise ValueError(f"返回数据异常：长度 {len(data) if isinstance(data, list) else 'N/A'}")
                return data
            except (urllib.error.HTTPError, urllib.error.URLError, ValueError, json.JSONDecodeError) as e:
                last_err = e
                log(f"  第 {i+1} 次失败：{e}")
                if i < max_retry - 1:
                    time.sleep(3 * (i + 1))
    raise RuntimeError(f"所有 Binance 镜像全失败：{last_err}")


def transform(klines):
    """Binance klines 行格式：[openTime_ms, o, h, l, c, vol, closeTime_ms, ...]"""
    dates, opens, highs, lows, closes = [], [], [], [], []
    for row in klines:
        if not row or len(row) < 5:
            continue
        ts_ms = row[0]
        date_str = datetime.datetime.fromtimestamp(ts_ms / 1000, tz=datetime.timezone.utc).strftime("%Y-%m-%d")
        dates.append(date_str)
        opens.append(float(row[1]))
        highs.append(float(row[2]))
        lows.append(float(row[3]))
        closes.append(float(row[4]))
    return {
        "dates": dates,
        "opens": opens,
        "highs": highs,
        "lows": lows,
        "closes": closes,
        "updated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "source": "binance_klines_1d_180",
    }


def sanity_check(new_data, old_data):
    """健全性检查：条数足够、末日不回退、收盘价合理"""
    if not new_data.get("dates"):
        return False, "新数据 dates 为空"
    if len(new_data["dates"]) < 150:
        return False, f"新数据条数 {len(new_data['dates'])} 异常少（应 >= 150）"
    if old_data and old_data.get("dates"):
        old_last = old_data["dates"][-1]
        new_last = new_data["dates"][-1]
        if new_last < old_last:
            return False, f"新末日期 {new_last} 早于旧末日期 {old_last}，疑似数据回退"
    last_close = new_data["closes"][-1]
    if not (10000 < last_close < 500000):
        return False, f"最新收盘 {last_close} 偏离合理区间(10k~500k)"
    return True, "ok"


def main():
    log("BTC K线数据更新开始")
    log(f"目标文件：{TARGET_FILE}")

    # 1. 读旧数据备份
    old_data = None
    if os.path.exists(TARGET_FILE):
        try:
            with open(TARGET_FILE, "r", encoding="utf-8") as f:
                old_data = json.load(f)
            log(f"旧数据：{len(old_data.get('dates', []))} 条，末日 {old_data.get('dates', ['N/A'])[-1]}")
        except Exception as e:
            log(f"⚠ 旧数据读取失败：{e}")

    # 2. 拉取新数据
    try:
        klines = fetch_binance()
        log(f"Binance 返回 {len(klines)} 条日K")
    except Exception as e:
        log(f"✘ 拉取失败，保留原文件不动：{e}")
        return 1

    # 3. 转换格式
    new_data = transform(klines)
    log(f"新数据：{len(new_data['dates'])} 条，{new_data['dates'][0]} ~ {new_data['dates'][-1]}")

    # 4. 健全性检查
    ok, reason = sanity_check(new_data, old_data)
    if not ok:
        log(f"✘ 健全性检查失败，保留原文件不动：{reason}")
        return 2

    # 5. 写文件
    try:
        with open(TARGET_FILE, "w", encoding="utf-8") as f:
            json.dump(new_data, f, ensure_ascii=False, separators=(",", ":"))
        log(f"✔ 写入成功，{len(new_data['dates'])} 条数据，末日 {new_data['dates'][-1]}，最新收盘 ${new_data['closes'][-1]:,.0f}")
    except Exception as e:
        log(f"✘ 写入失败：{e}")
        return 3

    # 6. Git 推送（可选，由调用方决定）
    if "--push" in sys.argv:
        import subprocess
        try:
            log("执行 git add/commit/push ...")
            subprocess.run(["git", "add", "btc_kline.json"], cwd=SCRIPT_DIR, check=True)
            subprocess.run(
                ["git", "commit", "-m", f"chore: BTC K线自动更新 ({new_data['dates'][-1]})"],
                cwd=SCRIPT_DIR, check=False
            )
            subprocess.run(["git", "push"], cwd=SCRIPT_DIR, check=True)
            log("✔ git push 完成")
        except Exception as e:
            log(f"⚠ git 操作失败（不影响本地数据）：{e}")

    log("BTC K线数据更新完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
