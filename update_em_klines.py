# -*- coding: utf-8 -*-
"""
update_em_klines.py — 4个EM品种(日经225/KOSPI/布伦特/AU9999)历史日K更新器
------------------------------------------------------------------
数据源：Wind get_index_kline（index_data，日K开收高低）
  - EM push2his 被公司网网络层封堵（本地/浏览器均不通），Wind 是唯一可用源
  - 日经225=N225.GI, KOSPI=KS11.GI, 布伦特=布伦特原油(自然语言), AU9999=AU9999
产物：n225_kline.json / ks11_kline.json / brent_kline.json / au9999_kline.json
  格式与 btc_kline.json 一致：dates/opens/highs/lows/closes/updated/source/name/unit
前端：index.html 读 JSON 画历史K线(T-1及以前)，今日 bar 由 EM 实时价拟合，YTD=现价/年初首收盘-1

用法：
  python update_em_klines.py            # 增量：拉最近15天合并进现有JSON（日常17:40自动化用）
  python update_em_klines.py --full     # 全量：从2020-01-01重建（首次/修复用）
  python update_em_klines.py --push     # 更新后执行 git add/commit + git_push_with_retry.py
健全性校验：条数下限、末日不回退、收盘价合理区间；失败保留旧文件不动
"""
import os
import sys
import json
import subprocess
import datetime

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
WIND_SKILL_DIR = os.path.expanduser(r"~\.agents\skills\wind-mcp-skill")
# node 优先用 WorkBuddy 托管运行时，fallback 到 PATH
NODE_CANDIDATES = [
    r"C:\Users\kelvinyye\.workbuddy\binaries\node\versions\22.22.2-3\node.exe",
    r"C:\Users\kelvinyye\.workbuddy\binaries\node\versions\22.22.2\node.exe",
    "node",
]

FULL_BEGIN = "2020-01-01"      # 全量起点（用户确认：从20年1月起）
INCR_DAYS = 30                 # 增量回看天数（覆盖节假日/漏跑/长假不开机；2026-09-17 由 15 上调）

INDICES = {
    "n225":   {"windcode": "N225.GI",   "file": "n225_kline.json",   "name": "日经225",     "unit": "点",     "lo": 10000, "hi": 100000},
    "ks11":   {"windcode": "KS11.GI",   "file": "ks11_kline.json",   "name": "韩国KOSPI",   "unit": "点",     "lo": 1000,  "hi": 10000},
    "brent":  {"windcode": "布伦特原油", "file": "brent_kline.json",  "name": "布伦特原油", "unit": "美元/桶", "lo": 5,     "hi": 500},
    "au9999": {"windcode": "AU9999",    "file": "au9999_kline.json", "name": "黄金AU9999",  "unit": "元/克",  "lo": 100,   "hi": 2000},
}


def log(msg):
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def find_node():
    for c in NODE_CANDIDATES:
        if c == "node":
            return c
        if os.path.exists(c):
            return c
    return "node"


def wind_kline(windcode, begin, end, retries=3):
    """调 Wind CLI 拉日K，返回 rows 列表 [[time,open,close,high,low,...],...]"""
    node = find_node()
    params = json.dumps({"windcode": windcode, "begin_date": begin, "end_date": end, "period": "1d"}, ensure_ascii=False)
    last_err = None
    for i in range(retries):
        try:
            r = subprocess.run(
                [node, "scripts/cli.mjs", "call", "index_data", "get_index_kline", params],
                cwd=WIND_SKILL_DIR, capture_output=True, text=True, timeout=120, encoding="utf-8", errors="replace",
            )
            if r.returncode != 0:
                raise RuntimeError(f"cli exit={r.returncode}: {(r.stderr or '')[-200:]}")
            outer = json.loads(r.stdout)
            inner = json.loads(outer["content"][0]["text"])
            rows = inner["data"]["rows"]
            if not rows:
                raise RuntimeError("Wind 返回空 rows")
            return rows
        except Exception as e:
            last_err = e
            log(f"  第{i+1}次拉取失败({windcode})：{e}")
    raise RuntimeError(f"Wind 拉取 {windcode} 连续{retries}次失败：{last_err}")


def rows_to_kv(rows):
    """Wind rows -> {date: (open,high,low,close)}；TIME 取前10字符，MATCH(idx2)=收盘"""
    kv = {}
    for row in rows:
        if not row or len(row) < 5:
            continue
        d = str(row[0])[:10]
        try:
            o, c, h, l = float(row[1]), float(row[2]), float(row[3]), float(row[4])
        except (TypeError, ValueError):
            continue
        kv[d] = (o, h, l, c)
    return kv


def load_existing(path):
    if not os.path.exists(path):
        return {}
    try:
        d = json.load(open(path, encoding="utf-8"))
        dates, opens = d.get("dates", []), d.get("opens", [])
        highs, lows, closes = d.get("highs", []), d.get("lows", []), d.get("closes", [])
        n = min(len(dates), len(opens), len(highs), len(lows), len(closes))
        return {dates[i]: (opens[i], highs[i], lows[i], closes[i]) for i in range(n)}
    except Exception as e:
        log(f"  ⚠ 旧JSON读取失败（视为空重建）：{e}")
        return {}


def update_one(key, cfg, full):
    path = os.path.join(SCRIPT_DIR, cfg["file"])
    today = datetime.date.today()
    if full:
        begin, end = FULL_BEGIN, today.strftime("%Y-%m-%d")
        log(f"[{key}] 全量模式：{begin} ~ {end}（{cfg['name']} / {cfg['windcode']}）")
    else:
        begin = (today - datetime.timedelta(days=INCR_DAYS)).strftime("%Y-%m-%d")
        end = today.strftime("%Y-%m-%d")
        log(f"[{key}] 增量模式：{begin} ~ {end}（{cfg['name']} / {cfg['windcode']}）")

    rows = wind_kline(cfg["windcode"], begin, end)
    new_kv = rows_to_kv(rows)
    log(f"[{key}] Wind 返回 {len(new_kv)} 条（{min(new_kv)} ~ {max(new_kv)}）")

    old_kv = {} if full else load_existing(path)
    before_last = max(old_kv) if old_kv else None

    merged = dict(old_kv)
    merged.update(new_kv)  # 同日期以新数据为准（Wind 可能修正未收盘bar）
    dates = sorted(merged.keys())

    # 健全性校验
    min_rows = 1500 if full else 1
    if len(dates) < min_rows:
        raise RuntimeError(f"[{key}] 条数 {len(dates)} 异常少（期望>={min_rows}），保留旧文件")
    if before_last and dates[-1] < before_last:
        raise RuntimeError(f"[{key}] 新末日 {dates[-1]} 早于旧末日 {before_last}，疑似回退，保留旧文件")
    last_close = merged[dates[-1]][3]
    if not (cfg["lo"] < last_close < cfg["hi"]):
        raise RuntimeError(f"[{key}] 末日收盘 {last_close} 超出合理区间({cfg['lo']}~{cfg['hi']})，保留旧文件")

    payload = {
        "dates": dates,
        "opens": [round(merged[d][0], 4) for d in dates],
        "highs": [round(merged[d][1], 4) for d in dates],
        "lows": [round(merged[d][2], 4) for d in dates],
        "closes": [round(merged[d][3], 4) for d in dates],
        "updated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "source": "wind_index_kline_1d",
        "name": cfg["name"],
        "unit": cfg["unit"],
        "windcode": cfg["windcode"],
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    log(f"[{key}] ✔ 写入 {cfg['file']}：{len(dates)} 条，末日 {dates[-1]} 收盘 {last_close}")


def git_push(files):
    try:
        subprocess.run(["git", "add"] + files, cwd=SCRIPT_DIR, check=True, capture_output=True)
        msg = f"chore: EM品种K线自动更新 ({datetime.date.today().strftime('%Y-%m-%d')})"
        subprocess.run(["git", "commit", "-m", msg], cwd=SCRIPT_DIR, check=False, capture_output=True)
        r = subprocess.run([sys.executable, "git_push_with_retry.py"], cwd=SCRIPT_DIR, capture_output=True, text=True, encoding="utf-8", errors="replace")
        log((r.stdout or "").strip())
        if r.returncode != 0:
            log(f"⚠ git push 脚本退出码 {r.returncode}（不影响本地数据）")
    except Exception as e:
        log(f"⚠ git 操作异常（不影响本地数据）：{e}")


def main():
    full = "--full" in sys.argv
    do_push = "--push" in sys.argv
    ok, fail = [], []
    for key, cfg in INDICES.items():
        try:
            update_one(key, cfg, full)
            ok.append(key)
        except Exception as e:
            log(f"[{key}] ✘ {e}")
            fail.append(key)
    log(f"完成：成功 {len(ok)} 个({','.join(ok) or '无'})，失败 {len(fail)} 个({','.join(fail) or '无'})")
    if do_push and ok:
        git_push([INDICES[k]["file"] for k in ok])
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
