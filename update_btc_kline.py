# -*- coding: utf-8 -*-
"""
BTC K线数据更新脚本
- 数据源：CoinGecko OHLC（180天，4天1根采样）
- 目标文件：btc_kline.json
- 兜底：CoinGecko 失败 -> 保留原文件不动，仅打印警告（避免坏数据覆盖好数据）
- 用法：python update_btc_kline.py
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
COINGECKO_URL = "https://api.coingecko.com/api/v3/coins/bitcoin/ohlc?vs_currency=usd&days=180"
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}


def log(msg):
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def fetch_coingecko(max_retry=3):
    last_err = None
    for i in range(max_retry):
        try:
            req = urllib.request.Request(COINGECKO_URL, headers=HEADERS)
            raw = urllib.request.urlopen(req, timeout=25).read()
            data = json.loads(raw)
            if not isinstance(data, list) or len(data) < 10:
                raise ValueError(f"返回数据异常：{type(data).__name__}, 长度 {len(data) if isinstance(data, list) else 'N/A'}")
            return data
        except (urllib.error.HTTPError, urllib.error.URLError, ValueError, json.JSONDecodeError) as e:
            last_err = e
            log(f"  CoinGecko 第 {i+1} 次失败：{e}")
            if i < max_retry - 1:
                time.sleep(5 * (i + 1))
    raise RuntimeError(f"CoinGecko 重试 {max_retry} 次全失败：{last_err}")


def transform(ohlc_list):
    """[[ts_ms, o, h, l, c], ...] -> {dates, opens, highs, lows, closes}"""
    dates, opens, highs, lows, closes = [], [], [], [], []
    for row in ohlc_list:
        if not row or len(row) < 5:
            continue
        ts_ms, o, h, l, c = row[:5]
        date_str = datetime.datetime.utcfromtimestamp(ts_ms / 1000).strftime("%Y-%m-%d")
        dates.append(date_str)
        opens.append(float(o))
        highs.append(float(h))
        lows.append(float(l))
        closes.append(float(c))
    return {
        "dates": dates,
        "opens": opens,
        "highs": highs,
        "lows": lows,
        "closes": closes,
        "updated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "source": "coingecko_ohlc_180d",
    }


def sanity_check(new_data, old_data):
    """新数据必须 >= 旧数据条数，最末日期必须 >= 旧末日期"""
    if not new_data.get("dates"):
        return False, "新数据 dates 为空"
    if len(new_data["dates"]) < 10:
        return False, f"新数据条数 {len(new_data['dates'])} 异常少"
    if old_data and old_data.get("dates"):
        old_last = old_data["dates"][-1]
        new_last = new_data["dates"][-1]
        if new_last < old_last:
            return False, f"新末日期 {new_last} 早于旧末日期 {old_last}，疑似数据回退"
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
        ohlc = fetch_coingecko()
        log(f"CoinGecko 返回 {len(ohlc)} 条 OHLC")
    except Exception as e:
        log(f"✘ 拉取失败，保留原文件不动：{e}")
        return 1

    # 3. 转换格式
    new_data = transform(ohlc)
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
        log(f"✔ 写入成功，{len(new_data['dates'])} 条数据，末日 {new_data['dates'][-1]}")
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
