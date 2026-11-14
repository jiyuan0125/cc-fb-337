#!/usr/bin/env python3
"""五分钟滑动窗口聚合（按 event_ts，事件时间 + 水位线）。

窗口：长度 300s，步进 60s，起点对齐整分钟（epoch 秒的整 60 倍）。
      一笔 event_ts = t 的交易属于窗口起点为 floor(t/60)*60 - 60*k (k=0..4) 的窗口。
水位：watermark = 已见最大 event_ts - 120s（两分钟容差）。
      event_ts <= watermark 的交易（含等于水位）判为迟到，进 side_output.csv。
      水位推过窗口终点（watermark >= window_start + 300）即关窗，关窗结果不再改。
      流结束时剩余窗口全部关闭。
输出：
  windows.csv      window_start,mch,count,amount      —— 每窗每商户笔数与金额（空窗不出行）
  side_output.csv  txn,mch,amt,event_ts,proc_ts       —— 迟到交易，保留原字段，按到达顺序
  running_total.csv mch,window_count,window_amount,side_count,side_amount,total_count,total_amount
                   —— 每商户累计：window（正常入窗）+ side（迟到）= total，三方可互推
"""

import csv
import json
import sys

WINDOW_SIZE = 300   # 5 分钟
STEP = 60           # 1 分钟步进
TOLERANCE = 120     # 水位容差 2 分钟


def main(stream_path="stream.jsonl", out_dir="."):
    open_windows = {}   # window_start -> {mch: [count, amount]}
    win_stats = {}      # mch -> [count, amount]  正常入窗累计（每笔计一次）
    side_stats = {}     # mch -> [count, amount]  迟到累计
    max_event_ts = None

    f_win = open(f"{out_dir}/windows.csv", "w", newline="")
    f_side = open(f"{out_dir}/side_output.csv", "w", newline="")
    win_w = csv.writer(f_win)
    side_w = csv.writer(f_side)
    win_w.writerow(["window_start", "mch", "count", "amount"])
    side_w.writerow(["txn", "mch", "amt", "event_ts", "proc_ts"])

    def close_window(start):
        """关窗并写出该窗结果；空窗不产出行。"""
        buckets = open_windows.pop(start, None)
        if not buckets:
            return
        for mch in sorted(buckets):
            cnt, amt = buckets[mch]
            win_w.writerow([start, mch, cnt, amt])

    def close_through(watermark):
        for start in sorted(open_windows):
            if start + WINDOW_SIZE <= watermark:
                close_window(start)

    with open(stream_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            mch, amt, event_ts = r["mch"], r["amt"], r["event_ts"]

            if max_event_ts is None or event_ts > max_event_ts:
                max_event_ts = event_ts
            watermark = max_event_ts - TOLERANCE

            if event_ts <= watermark:
                # 迟到（含等于水位）：原样进 side output，按到达顺序
                side_w.writerow([r["txn"], mch, amt, event_ts, r["proc_ts"]])
                s = side_stats.setdefault(mch, [0, 0])
                s[0] += 1
                s[1] += amt
            else:
                # 正常：落入包含它的全部窗口（最多 5 个）
                base = (event_ts // STEP) * STEP
                for k in range(WINDOW_SIZE // STEP):
                    start = base - k * STEP
                    buckets = open_windows.setdefault(start, {})
                    b = buckets.setdefault(mch, [0, 0])
                    b[0] += 1
                    b[1] += amt
                w = win_stats.setdefault(mch, [0, 0])
                w[0] += 1
                w[1] += amt

            close_through(watermark)

    # 流结束：关闭所有剩余窗口
    for start in sorted(open_windows):
        close_window(start)

    f_win.close()
    f_side.close()

    with open(f"{out_dir}/running_total.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["mch", "window_count", "window_amount",
                    "side_count", "side_amount",
                    "total_count", "total_amount"])
        for mch in sorted(set(win_stats) | set(side_stats)):
            wc, wa = win_stats.get(mch, [0, 0])
            sc, sa = side_stats.get(mch, [0, 0])
            w.writerow([mch, wc, wa, sc, sa, wc + sc, wa + sa])


if __name__ == "__main__":
    stream = sys.argv[1] if len(sys.argv) > 1 else "stream.jsonl"
    out = sys.argv[2] if len(sys.argv) > 2 else "."
    main(stream, out)
