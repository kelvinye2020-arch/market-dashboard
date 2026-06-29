#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_dr007.py — DR007 加权平均利率 JSON 增量更新器
------------------------------------------------------------------
指标：DR007(存款类机构质押式回购7天加权利率)   index_id = L001619493   单位 %   日频 T-1
数据源：同花顺 iFind EDB MCP (hexin-ifind-ds-edb-mcp)

【运行模式】见 build_cn10y.py 说明，逻辑完全一致。

【调用方式】
  python build_dr007.py --data '[["2026-06-26",1.4672],["2026-06-25",1.5098]]'
  echo '[["2026-06-26",1.4672]]' | python build_dr007.py --stdin
  python build_dr007.py --check

⚠️ 取数时务必区分 DR007(L001619493) 与 R007(M004039736)：
   - DR007 = 存款类机构质押式回购利率(仅银行，央行政策锚)  ← 本看板用这个
   - R007  = 全市场质押式回购利率(含非银，波动更大)        ← 不是这个！
   query 写"DR007加权平均利率，日频"，并核对返回 index_id == L001619493。
------------------------------------------------------------------
"""

import argparse
import json
import os
import sys
from datetime import datetime

INDEX_ID = "L001619493"
NAME = "DR007加权平均利率"
SOURCE = "iFind EDB"
UNIT = "%"
JSON_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dr007_rate.json")


def load_existing(path):
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        d = json.load(f)
    dates = d.get("dates", [])
    yields = d.get("yields", [])
    if len(dates) != len(yields):
        print(f"[WARN] 现有文件 dates({len(dates)}) != yields({len(yields)})，将以可对齐部分为准")
        n = min(len(dates), len(yields))
        dates, yields = dates[:n], yields[:n]
    return dict(zip(dates, yields))


def merge_and_write(new_rows, path):
    data_map = load_existing(path)
    before = len(data_map)

    added, updated = 0, 0
    for dt, val in new_rows:
        val = round(float(val), 4)
        if dt in data_map:
            if data_map[dt] != val:
                updated += 1
            data_map[dt] = val
        else:
            data_map[dt] = val
            added += 1

    items = sorted(data_map.items(), key=lambda x: x[0])
    dates = [k for k, _ in items]
    yields = [v for _, v in items]

    assert len(dates) == len(yields), f"FATAL: dates({len(dates)}) != yields({len(yields)})"

    payload = {
        "indexId": INDEX_ID,
        "name": NAME,
        "source": SOURCE,
        "unit": UNIT,
        "updated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "dates": dates,
        "yields": yields,
        "latest": yields[-1] if yields else None,
        "latest_date": dates[-1] if dates else None,
        "count": len(dates),
    }

    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print(f"[OK] 写入 {path}")
    print(f"     合并前 {before} 条 → 合并后 {len(dates)} 条 (新增 {added}, 更新 {updated})")
    print(f"     最新 {dates[-1]} = {yields[-1]}%")
    return 0


def check_only(path):
    if not os.path.exists(path):
        print(f"[ERR] 文件不存在: {path}")
        return 1
    with open(path, "r", encoding="utf-8") as f:
        d = json.load(f)
    dn, yn = len(d.get("dates", [])), len(d.get("yields", []))
    if dn == yn:
        print(f"[OK] 校验通过: dates={dn}, yields={yn}, 最新 {d.get('latest_date')}={d.get('latest')}%")
        return 0
    print(f"[ERR] 长度不匹配! dates={dn}, yields={yn}")
    return 1


def main():
    ap = argparse.ArgumentParser(description="dr007 JSON 增量更新器")
    ap.add_argument("--data", help='新数据 JSON 数组，如 \'[["2026-06-26",1.4672]]\'')
    ap.add_argument("--stdin", action="store_true", help="从 stdin 读 JSON 数组")
    ap.add_argument("--check", action="store_true", help="仅校验现有文件长度")
    args = ap.parse_args()

    if args.check:
        sys.exit(check_only(JSON_PATH))

    raw = None
    if args.stdin:
        raw = sys.stdin.read()
    elif args.data:
        raw = args.data
    else:
        print("用法: --data '[[\"date\",yield],...]' | --stdin | --check")
        sys.exit(1)

    try:
        new_rows = json.loads(raw)
        assert isinstance(new_rows, list) and all(len(r) == 2 for r in new_rows)
    except Exception as e:
        print(f"[ERR] 解析新数据失败: {e}")
        sys.exit(1)

    sys.exit(merge_and_write(new_rows, JSON_PATH))


if __name__ == "__main__":
    main()
