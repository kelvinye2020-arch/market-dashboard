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
     →【源① 首选，2026-11-01 打通】中国货币网「质押式回购日报·存款类机构」
         POST https://www.chinamoney.com.cn/ags/ms/cm-u-dlrp/PrDlyBltn
         表单 lang=en / indexType=markInterBankVOList / searchDate=YYYY-MM-DD
         取 records[].instrmntCd=="DR007" 的 wghtdAvgRepoRate（加权利率）
       ✅ 免费、无需积分、无人值守可跑；已交叉验证 2026-10-26~10-30 共 5 个交易日
         与库内 iFind/Wind 序列逐值完全一致。
       ⚠️ 只支持单日查询（区间按日循环）；日报 22:00-22:30 发布，故盘中只能取到 T-1。
     →【源② 降级】Wind EDB 指标代码 M1006337，走本地 Wind CLI（非 MCP）：
         cd ~/.agents/skills/wind-mcp-skill
         node scripts/cli.mjs call economic_data query_economic_indicator_data \
           '{"question":"M1006337","beginDate":"2026-08-27","endDate":"2026-09-09"}'
       ⚠️ 2026-11-01 起 Wind 账户积分余额不足，此源实质失效；
          脚本会嗅探 "积分余额不足" 并直接放弃（不再当抖动重试 3 次）。
     已排除的源（勿重踩）：
        - akshare.repo_rate_hist / repo_rate_query → 是 FDR007【定盘】利率，不是 DR007【加权平均】，禁用
        - R007 (M004039736) → 全市场含非银，与 DR007 不同指标，禁用
        - chinamoney /ags/ms/cm-u-bk-ccpr/ClPr 等接口已全部 404（2026-09-06 实测）
        - 东方财富 datacenter 无 DR007 报表；push2 域名在内网被封

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
import os
import shutil
import subprocess
import sys
import time
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


# ---------------------------------------------------------------------------
# DR007 源①（首选，2026-11-01 打通）：中国货币网「质押式回购日报·存款类机构」
#   接口：POST https://www.chinamoney.com.cn/ags/ms/cm-u-dlrp/PrDlyBltn
#         表单 lang=en & indexType=markInterBankVOList & searchDate=YYYY-MM-DD
#   取 records[].instrmntCd == "DR007" 的 wghtdAvgRepoRate（加权利率）
#   ✅ 口径验证（2026-11-01）：2026-10-26~10-30 共 5 个交易日与库内 iFind/Wind 序列
#      逐值完全一致（1.3766 / 1.3500 / 1.3412 / 1.3418 / 1.3898）。
#   ✅ 免费、无需积分、无人值守可跑，故取代 Wind CLI 成为首选兜底。
#   ⚠️ 两个特性（务必知悉）：
#      1) 接口只支持「单日」查询，区间需按日循环（一周约 7 次请求）。
#      2) 日报于每交易日 22:00-22:30 更新 → 17:35 的定时任务只能取到 T-1，
#         当日数据要等次日那轮才补得上，属预期（本看板本就是 T-1 口径）。
# ---------------------------------------------------------------------------
CM_DR007_URL = "https://www.chinamoney.com.cn/ags/ms/cm-u-dlrp/PrDlyBltn"
CM_DR007_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Referer": "https://www.chinamoney.com.cn/chinese/mtdexdaily/?tab=2",
}


def fetch_dr007_chinamoney(start: str, end: str):
    """DR007 加权平均利率 → 中国货币网质押式回购日报（存款类机构 DR 系列）。"""
    try:
        import requests
        import urllib3
        urllib3.disable_warnings()
    except ImportError:
        print("[dr007][SKIP] 未安装 requests，跳过中国货币网源", file=sys.stderr)
        return []

    d0 = datetime.strptime(start, "%Y%m%d")
    d1 = datetime.strptime(end, "%Y%m%d")
    rows, cur, fail, window_start = [], d0, 0, None

    while cur <= d1:
        ds = cur.strftime("%Y-%m-%d")
        cur += timedelta(days=1)
        try:
            resp = requests.post(
                CM_DR007_URL,
                data={"lang": "en", "indexType": "markInterBankVOList",
                      "searchDate": ds, "publishedTime": ""},
                headers=CM_DR007_HEADERS, timeout=20, verify=False,
            )
            payload = resp.json()
        except Exception as ex:
            fail += 1
            print(f"[dr007][WARN] 中国货币网 {ds} 请求异常: {ex}", file=sys.stderr)
            continue

        if str(payload.get("head", {}).get("rep_code")) != "200":
            # 非交易日/无成交时 rep_code 仍为 200 但 records 为空，这里只记真异常
            if payload.get("head", {}).get("rep_message"):
                print(f"[dr007][WARN] 中国货币网 {ds} 返回 "
                      f"{payload['head'].get('rep_message')}", file=sys.stderr)
            continue

        dd = payload.get("data") or {}
        if window_start is None and dd.get("startDate"):
            window_start = dd["startDate"]  # 接口只保留最近约 2 周，无法回溯补历史

        for rec in payload.get("records") or []:
            if rec.get("instrmntCd") == "DR007":
                v = rec.get("wghtdAvgRepoRate")
                if v not in (None, "", "null"):
                    rows.append([ds, round(float(v), 4)])
                break

    if fail:
        print(f"[dr007][WARN] 中国货币网共 {fail} 天请求失败", file=sys.stderr)
    rows.sort(key=lambda x: x[0])
    if rows:
        print(f"[dr007][OK] 中国货币网取到 {len(rows)} 条：{rows[0][0]} ~ {rows[-1][0]}",
              file=sys.stderr)
    else:
        msg = "[dr007][WARN] 中国货币网未取到 DR007"
        if window_start:
            msg += (f"（接口仅提供 {window_start} 起约 2 周的滚动窗口，"
                    f"更早的缺口此源补不了，需走 Wind/iFind）")
        print(msg, file=sys.stderr)
    return rows


# ---------------------------------------------------------------------------
# DR007 源②（降级）：Wind EDB M1006337，走本地 CLI
# ⚠️ 2026-11-01 起 Wind 账户积分余额不足，此源实质失效，仅在源①无数据时尝试。
# ---------------------------------------------------------------------------
WIND_SKILL_DIR = os.path.expanduser("~/.agents/skills/wind-mcp-skill")
WIND_DR007_CODE = "M1006337"  # Wind EDB「DR007」，来源中国货币网，日频
# Wind CLI 偶发瞬时失败（2026-09-16 实测：首次退出码 1 且 stderr 为空，重试即成功），
# 故对 CLI 调用做重试，避免因一次抖动就判定"取数为空"。
WIND_CLI_RETRIES = 3
WIND_CLI_RETRY_SLEEP = 3


def _find_node() -> str:
    for cand in (
        shutil.which("node"),
        os.path.expandvars(r"%ProgramFiles%\nodejs\node.exe"),
        r"C:/Users/kelvinyye/.workbuddy/binaries/node/versions/22.22.2-2/node.exe",
    ):
        if cand and os.path.exists(cand):
            return cand
    return ""


def fetch_dr007(start: str, end: str):
    """DR007 加权平均利率：源①中国货币网（免费/首选）→ 源②Wind CLI（降级）。"""
    rows = fetch_dr007_chinamoney(start, end)
    if rows:
        return rows
    print("[dr007][INFO] 源①无数据，降级尝试 Wind CLI", file=sys.stderr)
    return _fetch_dr007_wind(start, end)


def _fetch_dr007_wind(start: str, end: str):
    """DR007 加权平均利率 → Wind EDB M1006337（走本地 CLI，不依赖 MCP）。"""
    if not os.path.isdir(WIND_SKILL_DIR):
        print(f"[dr007][SKIP] 未找到 Wind skill 目录 {WIND_SKILL_DIR}", file=sys.stderr)
        return []
    node = _find_node()
    if not node:
        print("[dr007][SKIP] 未找到可用的 node 可执行文件", file=sys.stderr)
        return []

    b, e = f"{start[:4]}-{start[4:6]}-{start[6:]}", f"{end[:4]}-{end[4:6]}-{end[6:]}"
    payload = json.dumps(
        {"question": WIND_DR007_CODE, "beginDate": b, "endDate": e}, ensure_ascii=False
    )
    res = None
    for attempt in range(1, WIND_CLI_RETRIES + 1):
        try:
            r = subprocess.run(
                [node, "scripts/cli.mjs", "call", "economic_data",
                 "query_economic_indicator_data", payload],
                cwd=WIND_SKILL_DIR, capture_output=True, text=True, timeout=120,
                encoding="utf-8",
            )
        except Exception as ex:
            print(f"[dr007][WARN] 第 {attempt}/{WIND_CLI_RETRIES} 次 Wind CLI 调用异常: {ex}",
                  file=sys.stderr)
            time.sleep(WIND_CLI_RETRY_SLEEP)
            continue

        if r.returncode == 0 and (r.stdout or "").strip():
            res = r
            break
        out = (r.stdout or "") + (r.stderr or "")
        # ⚠️ 积分耗尽与网络抖动的日志表现完全一致（都是退出码 1 + 空 stderr），
        #    必须先从 stdout 里嗅探真实原因，避免把"没钱了"当抖动重试 3 次。
        if ("积分余额不足" in out) or ("backend_error" in out and "credits" in out.lower()):
            print("[dr007][ERR] Wind 账户积分余额不足（不是网络抖动），源②失效。"
                  "充值入口 https://market.windalice.com/#/user/credits", file=sys.stderr)
            return []
        print(f"[dr007][WARN] 第 {attempt}/{WIND_CLI_RETRIES} 次 Wind CLI 失败 "
              f"(退出码 {r.returncode}): {(r.stderr or '').strip()[:200]}",
              file=sys.stderr)
        time.sleep(WIND_CLI_RETRY_SLEEP)

    if res is None:
        print(f"[dr007][ERR] Wind CLI 连续 {WIND_CLI_RETRIES} 次失败，本次跳过 dr007",
              file=sys.stderr)
        return []

    try:
        wrapper = json.loads(res.stdout)
        inner = json.loads(wrapper["content"][0]["text"])
        m = inner["metrics"][0]
        name, dates, values = m["meta"]["name"], m["date"], m["value"]
    except Exception as ex:
        print(f"[dr007][ERR] Wind CLI 返回解析失败: {ex}; "
              f"原始输出前 300 字: {res.stdout[:300]}", file=sys.stderr)
        return []

    if name.strip().upper() != "DR007":
        print(f"[dr007][ERR] 指标名校验失败，取到 {name!r}，期望 DR007", file=sys.stderr)
        return []

    rows = []
    for d, v in zip(dates, values):
        if v is None:
            continue
        rows.append([f"{str(d)[:4]}-{str(d)[4:6]}-{str(d)[6:8]}", round(float(v), 4)])

    rows.sort(key=lambda x: x[0])
    print(f"[dr007][OK] 取到 {len(rows)} 条："
          f"{rows[0][0]} ~ {rows[-1][0]}" if rows else "[dr007][WARN] Wind 返回空序列",
          file=sys.stderr)
    return rows


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
