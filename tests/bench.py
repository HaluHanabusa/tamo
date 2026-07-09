"""tamo 性能ベンチマーク — 10万イベント規模での応答速度実測。

実コードパス(put_raw + upsert_event + FTS登録)でシードし、
利用者が体感する操作のレイテンシを測る。
usage: python3 tests/bench.py <TAMO_HOME> [sessions] [events_per_session]
"""
from __future__ import annotations

import json
import random
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tamo.schema import make_event  # noqa: E402
from tamo.store import Store  # noqa: E402

JP = ["甲板カバーの連動条件を確認", "クレーン旋回の制約を整理", "決定: EARS記法で書く",
      "スナップショット方式を採用", "係留試験の張力データ", "バラスト調整の手順見直し",
      "ハッチ開閉トルクの実測値", "検査記録のフォーマット統一", "艤装工程の依存関係"]
EN = ["refactor the collector pipeline", "fix cursor rowid handling", "deterministic event id",
      "add trigram index for search", "budget packing with MMR", "quarantine broken lines"]


def seed(store: Store, n_sessions: int, n_events: int) -> float:
    rng = random.Random(42)
    base = datetime(2026, 5, 10, tzinfo=timezone.utc)
    t0 = time.perf_counter()
    total = 0
    for si in range(n_sessions):
        sk = f"bench_{si:04d}"
        for ei in range(n_events):
            ts = (base + timedelta(minutes=si * 7 + ei * 3)).isoformat(timespec="seconds")
            text = f"{rng.choice(JP)} / {rng.choice(EN)} #{si}-{ei} " + "文脈 " * rng.randint(5, 30)
            raw_id, _ = store.put_raw("claude_code", f"bench://{sk}#{ei}", text.encode())
            ev = make_event(
                source_kind="claude_code", session_key=f"claude_code:{sk}",
                native_id=f"{sk}-{ei}", seq=ei, ts=ts,
                actor="user" if ei % 2 == 0 else "assistant", kind="message",
                content=[{"type": "text", "text": text}],
                locator=f"bench://{sk}#{ei}",
            )
            store.upsert_event(ev, raw_id)
            total += 1
            if total % 2000 == 0:
                store.commit()
    store.commit()
    return time.perf_counter() - t0


def lap(fn, n=5) -> float:
    best = 1e9
    for _ in range(n):
        t = time.perf_counter()
        fn()
        best = min(best, time.perf_counter() - t)
    return best * 1000  # ms


if __name__ == "__main__":
    home = Path(sys.argv[1])
    ns = int(sys.argv[2]) if len(sys.argv) > 2 else 200
    ne = int(sys.argv[3]) if len(sys.argv) > 3 else 500
    store = Store(home)
    n = ns * ne
    if store.stats()["events"] < n:
        dt = seed(store, ns, ne)
        print(f"seed: {n}events in {dt:.1f}s ({n/dt:.0f} ev/s)")
    else:
        print(f"(seed済み {store.stats()['events']}events を再利用)")

    r = {}
    r["search_rare_ms"] = lap(lambda: store.search("トルクの実測", limit=10))
    r["search_common_ms"] = lap(lambda: store.search("文脈", limit=10))
    r["tail5_latest_ms"] = lap(lambda: store.iter_session_events(
        store.latest_session_key(), tail=5))
    r["since_event_mid_ms"] = lap(lambda: store.iter_session_events(
        "claude_code:bench_0100",
        since_event_id=store.iter_session_events("claude_code:bench_0100", tail=250)[0]["event_id"]))
    from tamo import mcp_server as m
    import os
    os.environ["TAMO_HOME"] = str(home)
    r["mcp_get_session_compact_ms"] = lap(lambda: m.get_session("latest:claude_code", max_events=200), n=3)
    r["mcp_search_ms"] = lap(lambda: m.search_context("係留試験", limit=5), n=3)

    from tamo.optimize import build_pack
    evs = store.recent_events(days=60, limit=5000)
    t = time.perf_counter()
    pack = build_pack(evs, budget_tokens=6000, query="決定 クレーン")
    r["pack_5000ev_ms"] = (time.perf_counter() - t) * 1000

    s = store.stats()
    r["db_mb"] = round(s["db_bytes"] / 1e6, 1)
    r["events"] = s["events"]
    print(json.dumps(r, ensure_ascii=False, indent=1))
