#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch_em_quotes.py — 东方财富实时行情批量抓取（日经/KOSPI/布伦特/AU9999）
输出 em_quotes.json 供前端看板读取（公司浏览器无法直连 EM API，走自动化绕行）

使用方式：
  python fetch_em_quotes.py              # 抓取并写文件
  python fetch_em_quotes.py --check      # 仅校验现有 JSON

数据源：push2delay.eastmoney.com（2分钟延迟，bash 可达，浏览器不可达）
"""
import json, os, sys, urllib.request, time
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT = os.path.join(SCRIPT_DIR, "em_quotes.json")

# 4个品种定义：secid, 小数位, 显示名
INDICES = [
    {"key": "em_N225",  "secid": "100.N225",   "decimals": 2, "name": "日经225"},
    {"key": "em_KS11",  "secid": "100.KS11",   "decimals": 2, "name": "韩国KOSPI"},
    {"key": "em_BRENT", "secid": "112.B00Y",   "decimals": 2, "name": "布伦特原油"},
    {"key": "em_AU9999","secid": "118.AU9999",  "decimals": 2, "name": "黄金AU9999"},
]

FIELDS = "f43,f44,f45,f46,f47,f48,f50,f51,f52,f55,f57,f58,f60,f170,f171"
HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.eastmoney.com/"}
TIMEOUT = 10


def fetch_one(secid, max_retry=2):
    """拉取单个品种的实时行情，返回 dict 或 None"""
    for attempt in range(max_retry):
        try:
            url = f"https://push2delay.eastmoney.com/api/qt/stock/get?secid={secid}&fields={FIELDS}&_={int(time.time()*1000)}"
            req = urllib.request.Request(url, headers=HEADERS)
            resp = urllib.request.urlopen(req, timeout=TIMEOUT)
            raw = json.loads(resp.read().decode("utf-8"))
            if not raw or not raw.get("data") or not raw["data"].get("f43"):
                raise ValueError("empty data")
            return raw["data"]
        except Exception as e:
            if attempt < max_retry - 1:
                time.sleep(2)
            else:
                print(f"  [{secid}] 拉取失败(重试{max_retry}次): {e}")
    return None


def parse(data, decimals):
    """解析东方财富行情数据字段"""
    divisor = pow(10, decimals)
    price = data.get("f43", 0) / divisor
    prev   = data.get("f60", 0) / divisor
    chg_pct= data.get("f170", 0) / 100  # f170=涨跌幅*100
    chg    = price - prev if prev > 0 else 0
    return {
        "price": round(price, decimals),
        "prev_close": round(prev, decimals),
        "change": round(chg, decimals),
        "change_pct": round(chg_pct, 2),
        "open": round(data.get("f46", 0) / divisor, decimals),
        "high": round(data.get("f44", 0) / divisor, decimals),
        "low":  round(data.get("f45", 0) / divisor, decimals),
        "volume": data.get("f47", 0),
        "amount": data.get("f48", 0),
    }


def sanity_check(quotes):
    """健全性：至少2个品种有非零价格，无极端涨跌幅"""
    ok = 0
    for k, q in quotes.items():
        if q and q.get("price", 0) > 0:
            ok += 1
        if q and abs(q.get("change_pct", 0)) > 30:
            print(f"[WARN] {k} 涨跌幅 {q['change_pct']}% 异常（>30%），但继续")
    if ok < 2:
        print(f"[ERROR] 仅 {ok} 个品种有效（需 ≥2），拒绝覆盖")
        return False
    return True


def main():
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] 开始拉取 4 个 EM 实时行情...")

    quotes = {}
    for idx in INDICES:
        raw = fetch_one(idx["secid"])
        if raw:
            quotes[idx["key"]] = parse(raw, idx["decimals"])
            print(f"  [{idx['name']}] {quotes[idx['key']]['price']}  ({quotes[idx['key']]['change_pct']:+.2f}%)")
        else:
            quotes[idx["key"]] = None
            print(f"  [{idx['name']}] 失败")

    if not sanity_check(quotes):
        return 1

    payload = {
        "updated": now,
        "quotes": quotes,
    }
    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))

    print(f"[OK] 写入 {OUTPUT}")
    print(f"     {sum(1 for q in quotes.values() if q)}/4 成功")
    return 0


def check():
    if not os.path.exists(OUTPUT):
        print(f"[ERR] 文件不存在: {OUTPUT}")
        return 1
    with open(OUTPUT, "r", encoding="utf-8") as f:
        d = json.load(f)
    q = d.get("quotes", {})
    print(f"  em_quotes.json 更新于 {d.get('updated')}")
    for k in ["em_N225","em_KS11","em_BRENT","em_AU9999"]:
        v = q.get(k)
        if v:
            print(f"  {k}: {v['price']} ({v['change_pct']:+.2f}%)")
        else:
            print(f"  {k}: 无数据")
    return 0


if __name__ == "__main__":
    if "--check" in sys.argv:
        sys.exit(check())
    sys.exit(main())
