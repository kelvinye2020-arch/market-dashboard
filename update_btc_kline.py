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
# 兜底源（2026-10-06 新增）：Binance 全镜像被公司网络 SSL 拦截时启用。
# Gate.io 日K同为 UTC 0 点开盘，与 Binance 收盘价偏差 <=0.02%（已交叉验证 15 天），可安全替代。
GATE_URLS = [
    "https://api.gateio.ws/api/v4/spot/candlesticks?currency_pair=BTC_USDT&interval=1d&limit=180",
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


def fetch_gate(max_retry=3):
    """兜底源 Gate.io，返回与 Binance klines 同构的标准化行（含未完成标记过滤）"""
    last_err = None
    for url in GATE_URLS:
        log(f"尝试兜底源：{url.split('/api/')[0]}")
        for i in range(max_retry):
            try:
                req = urllib.request.Request(url, headers=HEADERS)
                raw = urllib.request.urlopen(req, timeout=25).read()
                data = json.loads(raw)
                if not isinstance(data, list) or len(data) < 30:
                    raise ValueError(f"返回数据异常：长度 {len(data) if isinstance(data) else 'N/A'}")
                rows = []
                for r in data:
                    # Gate 行：[ts_sec, 报价成交量, close, high, low, open, 基础成交量, 是否收盘]
                    if not r or len(r) < 7:
                        continue
                    # 只取已收盘K线（flag=false 为当日未完成K）
                    if len(r) > 7 and str(r[7]).lower() != "true":
                        continue
                    open_ms = int(float(r[0])) * 1000
                    rows.append([
                        open_ms,
                        float(r[5]),  # open
                        float(r[3]),  # high
                        float(r[4]),  # low
                        float(r[2]),  # close
                        float(r[6]),  # volume
                        open_ms + 86400000 - 1,  # closeTime
                    ])
                if len(rows) < 150:
                    raise ValueError(f"Gate 收盘K线仅 {len(rows)} 条，异常少")
                return rows
            except (urllib.error.HTTPError, urllib.error.URLError, ValueError,
                    json.JSONDecodeError, IndexError, TypeError) as e:
                last_err = e
                log(f"  第 {i+1} 次失败：{e}")
                if i < max_retry - 1:
                    time.sleep(3 * (i + 1))
    raise RuntimeError(f"Gate 兜底源全失败：{last_err}")


def transform(klines, source_tag="binance_klines_1d_180"):
    """Binance klines 行格式：[openTime_ms, o, h, l, c, vol, closeTime_ms, ...]"""
    dates, opens, highs, lows, closes = [], [], [], [], []
    now_ms = time.time() * 1000
    for row in klines:
        if not row or len(row) < 7:
            continue
        # 排除尚未收盘的日K（今天这根），只保留已收盘的完整日K —— 口径统一为"最近一个完整收盘日"(T-1)
        if row[6] > now_ms:
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
        "source": source_tag,
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
    # 换源后的跨源一致性：与旧末日收盘价相比，日涨跌 >=5% 只告警不拦截（BTC 确有单日 5%+ 波动）
    if old_data and old_data.get("closes") and old_data["dates"][-1] != new_data["dates"][-1]:
        old_close = old_data["closes"][-1]
        if old_close:
            chg = (last_close / old_close - 1) * 100
            if abs(chg) >= 5:
                log(f"⚠ 最新收盘较上一交易日变动 {chg:+.2f}%（>=5%），建议人工复核是否为脏数据")
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

    # 2. 拉取新数据（Binance 主源，失败降级 Gate.io 兜底源）
    source_tag = "binance_klines_1d_180"
    try:
        klines = fetch_binance()
        log(f"Binance 返回 {len(klines)} 条日K")
    except Exception as e:
        log(f"⚠ Binance 主源失败：{e}")
        try:
            klines = fetch_gate()
            source_tag = "gate_klines_1d_180"
            log(f"✔ Gate 兜底源返回 {len(klines)} 条已收盘日K")
        except Exception as e2:
            log(f"✘ 拉取失败，保留原文件不动：{e2}")
            return 1

    # 3. 转换格式
    new_data = transform(klines, source_tag)
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
