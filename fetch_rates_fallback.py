#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch_rates_fallback.py — 利率数据取数兜底脚本（iFind MCP 不可用时使用）
==========================================================================
背景：market-dashboard-rates-update 自动化依赖 iFind EDB MCP
      (hexin-ifind-ds-edb-mcp / get_edb_data)。该 MCP 在无人值守的定时会话中
      自 2026-09-02 起连续无法加载（ToolSearch 报 "No matching tools found"）。
      本脚本用公开官方源兜底取数，避免自动化长期空转。

数据源与口径
  A) 中债国债到期收益率:10年  (iFind EDB index_id = L001619604, 单位 %, 日频 T-1)
     → 中国债券信息网 chinabond「中债国债收益率曲线」10年
     → 通过 akshare.bond_china_yield() 抓取
     ✅ 已交叉验证：与 iFind 历史序列同日期值完全一致（2026-08-26 = 1.6887）

  B) DR007 加权平均利率      (iFind EDB index_id = L001619493, 单位 %, 日频 T-1)
     ⚠️ 暂无可用公开源。已排除：
        - akshare.repo_rate_hist / repo_rate_query → 是 FDR007【定盘】利率，不是 DR007【加权平均】，禁用
        - R007 (M004039736) → 全市场含非银，与 DR007 不同指标，禁用
        - chinamoney /ags/ms/cm-u-bk-ccpr/ClPr 等接口已全部 404（2026-09-06 实测）
        - 东方财富 datacenter 无 DR007 报表；push2 域名在内网被封
     结论：DR007 仍只能走 iFind MCP，本脚本返回空。

运行（必须用装了 akshare 的 venv）
  C:/Users/kelvinyye/.workbuddy/binaries/python/envs/default/Scripts/python.exe \
      fetch_rates_fallback.py --start 20260820 --end 20260906

输出：stdout 打印可直接喂给 build_*.py 的 JSON，例如
  {"cn10y": [["2026-08-27", 1.6988], ...], "dr007": []}
退出码：0 = 至少一个指标取到；1 = 全部取空。
==========================================================================
"""

import argparse
import json
import sys
from datetime import datetime, timedelta


def norm_date(s: str) -> str:
    """统一归一为 YYYYMMDD。兼容 '2026-09-08' / '2026/09/08' / '20260908'。
    注意：akshare.bond_china_yield 只吃 YYYYMMDD，传带分隔符的日期会拼出
    非法 URL，最终表现为 pandas 'No tables found'（2026-09-08 踩坑）。"""
    t = str(s).strip().replace("-", "").replace("/", "")
    if len(t) != 8 or not t.isdigit():
        raise ValueError(f"无法识别的日期格式: {s!r}（期望 YYYYMMDD 或 YYYY-MM-DD）")
    return t


def fetch_cn10y(start: str, end: str):
    """中债国债收益率曲线·10年。返回 [[date, value], ...]"""
    try:
        import akshare as ak
    except ImportError:
        print("[cn10y][ERR] 未安装 akshare，请用 venv 运行", file=sys.stderr)
        return []

    df = ak.bond_china_yield(start_date=start, end_date=end)
    if df is None or df.empty:
        print("[cn10y][WARN] akshare 返回空", file=sys.stderr)
        return []

    d = df[df["曲线名称"] == "中债国债收益率曲线"][["日期", "10年"]].copy()
    if d.empty:
        print("[cn10y][WARN] 未匹配到「中债国债收益率曲线」", file=sys.stderr)
        return []

    rows = []
    for _, r in d.iterrows():
        date = str(r["日期"])[:10]
        val = r["10年"]
        if val is None or str(val).lower() == "nan":
            continue
        rows.append([date, round(float(val), 4)])

    rows.sort(key=lambda x: x[0])
    print(f"[cn10y][OK] 取到 {len(rows)} 条：{rows[0][0]} ~ {rows[-1][0]}", file=sys.stderr)
    return rows


def fetch_dr007(start: str, end: str):
    """DR007 加权平均利率。当前无可用公开源，恒返回空。"""
    print(
        "[dr007][SKIP] 无可用公开源（FDR007/R007 均非本指标，chinamoney 接口 404）。"
        "请走 iFind EDB MCP (index_id=L001619493)。",
        file=sys.stderr,
    )
    return []


def main():
    today = datetime.now()
    p = argparse.ArgumentParser()
    p.add_argument("--start", default=(today - timedelta(days=14)).strftime("%Y%m%d"),
                   help="开始日期 YYYYMMDD，默认 14 天前")
    p.add_argument("--end", default=today.strftime("%Y%m%d"), help="结束日期 YYYYMMDD")
    p.add_argument("--only", choices=["cn10y", "dr007"], default=None)
    a = p.parse_args()
    a.start, a.end = norm_date(a.start), norm_date(a.end)

    out = {"cn10y": [], "dr007": []}
    if a.only in (None, "cn10y"):
        out["cn10y"] = fetch_cn10y(a.start, a.end)
    if a.only in (None, "dr007"):
        out["dr007"] = fetch_dr007(a.start, a.end)

    print(json.dumps(out, ensure_ascii=False))
    return 0 if (out["cn10y"] or out["dr007"]) else 1


if __name__ == "__main__":
    sys.exit(main())
