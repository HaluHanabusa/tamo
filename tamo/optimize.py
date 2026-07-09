"""tamo.optimize — 決定論的な引き継ぎ最適化（LLM/エージェント呼び出しゼロ）。

方針: 「取込時は無損失、読出時に最適化」。
raw層が常に残るため、ここでの圧縮はすべて再構築可能な"ビュー"にすぎない。

パス構成:
  P1 exact-dedup      … 完全一致ブロックの重複排除（同じツール出力/定型文）
  P2 snapshot-collapse… 同一ファイルの読取/書込スナップショット群を
                        「最終版フル + それ以前はunified diff」に折り畳み
  P3 salience        … 規則ベースの顕在情報抽出（決定/制約/TODO/エラー→修正/成果物）
  P4 pack            … トークン予算内で TF-IDF + MMR により選抜しMarkdown組版

P3の抽出は意味理解ではなくパターン照合であり、意味的な蒸留（KU化）は
下流のHITLパイプライン（例: OmniBrain）に委ねる、という役割分担。
"""
from __future__ import annotations

import difflib
import math
import re
from collections import defaultdict

from .schema import blocks_text
from .util import estimate_tokens, strip_noise, tokenize, truncate

# ------------------------------------------------------------------ P1 dedup

def dedup_events(events: list[dict]) -> tuple[list[dict], dict]:
    seen: dict[str, str] = {}
    out: list[dict] = []
    saved_chars = 0
    n_dup = 0
    for ev in events:
        text = blocks_text(ev.get("content", []))
        key = text.strip()
        if len(key) >= 120 and key in seen:
            n_dup += 1
            saved_chars += len(key)
            ev2 = dict(ev)
            ev2["content"] = [{"type": "text", "text": f"[重複省略: e:{seen[key][:8]} と同一 ({len(key)}字)]"}]
            ev2["_deduped"] = True
            out.append(ev2)
        else:
            seen.setdefault(key, ev["event_id"])
            out.append(ev)
    return out, {"dup_events": n_dup, "saved_chars": saved_chars}


# -------------------------------------------------------- P2 snapshot collapse

def collapse_snapshots(events: list[dict], diff_limit: int = 3500) -> tuple[list[dict], dict]:
    """hints.file_path 付き tool_result（同一ファイルの版）を diff 折り畳み。"""
    by_file: dict[str, list[int]] = defaultdict(list)
    for i, ev in enumerate(events):
        fp = ev.get("hints", {}).get("file_path")
        if fp and ev.get("kind") == "tool_result" and not ev.get("_deduped"):
            by_file[fp].append(i)
    out = list(events)
    collapsed = 0
    saved_chars = 0
    for fp, idxs in by_file.items():
        if len(idxs) < 2:
            continue
        texts = [blocks_text(events[i].get("content", [])) for i in idxs]
        for k in range(len(idxs) - 1):  # 最後の版だけフルで残す
            i = idxs[k]
            cur, nxt = texts[k], texts[k + 1]
            if len(cur) < 400:
                continue
            diff = "\n".join(difflib.unified_diff(
                cur.splitlines(), nxt.splitlines(),
                fromfile=f"{fp} (v{k + 1})", tofile=f"{fp} (v{k + 2})", lineterm="", n=2,
            ))
            body = truncate(diff, diff_limit, "\n…[diff省略]")
            ev2 = dict(out[i])
            ev2["content"] = [{"type": "text", "text":
                f"[snapshot折り畳み {fp} v{k + 1}: {len(cur)}字 → 次版とのdiffのみ]\n{body}"}]
            saved_chars += max(0, len(cur) - len(body))
            out[i] = ev2
            collapsed += 1
    return out, {"snapshots_collapsed": collapsed, "saved_chars": saved_chars}


# ---------------------------------------------------------------- P3 salience

_RULES: list[tuple[str, re.Pattern]] = [
    ("decision", re.compile(r"(方針|決定|採用|廃止|やめて|でいく|にする|に決め|Decision|decided|we(?:'|’)ll (?:use|go)|instead of|adopt(?:ed)?)", re.I)),
    ("todo", re.compile(r"(TODO|FIXME|やること|残タスク|次にやる|next step|remaining)", re.I)),
    ("constraint", re.compile(r"(必ず|してはならない|しないこと|禁止|前提|制約|must(?: not)?|never|always|constraint)", re.I)),
    ("error", re.compile(r"(Traceback|Exception|Error[:\s]|エラー|失敗|exit code [1-9]|FAILED)", re.I)),
]
_FIX = re.compile(r"(修正|解決|直した|直す|対応した|fixed|resolved|passed|成功|works now)", re.I)


def _match_lines(text: str, pat: re.Pattern, cap: int = 240) -> list[str]:
    hits = []
    for ln in text.splitlines():
        ln = ln.strip()
        if ln and pat.search(ln):
            hits.append(truncate(ln, cap))
        if len(hits) >= 4:
            break
    return hits


def extract_salience(events: list[dict]) -> dict[str, list[dict]]:
    cats: dict[str, list[dict]] = {"decision": [], "constraint": [], "todo": [], "error_fix": [], "artifact": []}
    pending_errors: list[tuple[int, str, str]] = []  # (idx, line, eid)
    last_write: dict[str, str] = {}
    for i, ev in enumerate(events):
        if ev.get("_deduped"):
            continue
        text = strip_noise(blocks_text(ev.get("content", [])))
        eid = ev["event_id"]
        for cat, pat in _RULES:
            if cat == "error":
                continue
            for ln in _match_lines(text, pat):
                cats[cat].append({"text": ln, "eid": eid, "idx": i})
        for ln in _match_lines(text, _RULES[3][1], cap=200):
            pending_errors.append((i, ln, eid))
        if ev.get("actor") == "assistant" and pending_errors and _FIX.search(text):
            fix_line = next((truncate(ln.strip(), 200) for ln in text.splitlines() if _FIX.search(ln)), "")
            for _, err_ln, err_eid in pending_errors[-2:]:
                cats["error_fix"].append({"text": f"ERR: {err_ln}\n → FIX: {fix_line}", "eid": f"{err_eid},{eid}", "idx": i})
            pending_errors.clear()
        # 成果物: 書込系tool_useの最終パス
        for b in ev.get("content", []):
            if b.get("type") == "tool_use" and b.get("name") in ("Write", "Edit", "MultiEdit", "create_file", "str_replace"):
                fp = (b.get("input") or {}).get("file_path") or (b.get("input") or {}).get("path")
                if fp:
                    last_write[fp] = eid
    for i, (_, ln, eid) in enumerate(pending_errors[-3:]):
        cats["error_fix"].append({"text": f"ERR(未解決): {ln}", "eid": eid, "idx": 10**9})
    cats["artifact"] = [{"text": fp, "eid": eid, "idx": 10**9} for fp, eid in last_write.items()]
    # カテゴリ内の重複行を除去
    for cat, rows in cats.items():
        seen = set()
        uniq = []
        for r in rows:
            k = r["text"]
            if k not in seen:
                seen.add(k)
                uniq.append(r)
        cats[cat] = uniq
    return cats


# --------------------------------------------------------------- P4 pack

def _tfidf_vectors(texts: list[str]) -> list[dict[str, float]]:
    docs = [tokenize(t) for t in texts]
    df: dict[str, int] = defaultdict(int)
    for d in docs:
        for t in set(d):
            df[t] += 1
    n = max(1, len(docs))
    vecs = []
    for d in docs:
        tf: dict[str, float] = defaultdict(float)
        for t in d:
            tf[t] += 1.0
        v = {t: (c / len(d)) * math.log(1 + n / df[t]) for t, c in tf.items()} if d else {}
        vecs.append(v)
    return vecs


def _cos(a: dict[str, float], b: dict[str, float]) -> float:
    if not a or not b:
        return 0.0
    if len(a) > len(b):
        a, b = b, a
    dot = sum(w * b.get(t, 0.0) for t, w in a.items())
    na = math.sqrt(sum(w * w for w in a.values()))
    nb = math.sqrt(sum(w * w for w in b.values()))
    return dot / (na * nb) if na and nb else 0.0


def build_pack(events: list[dict], *, budget_tokens: int = 6000, query: str = "",
               title: str = "tamo context pack") -> tuple[str, dict]:
    """イベント列 → 予算内Markdownパック。決定論（同入力→同出力）。"""
    events = sorted(events, key=lambda e: (e.get("ts") or "", e.get("seq") or 0))
    events, st1 = dedup_events(events)
    events, st2 = collapse_snapshots(events)
    sal = extract_salience(events)

    lines: list[str] = [f"# {title}", ""]
    used = estimate_tokens("\n".join(lines))

    def emit(section: str, rows: list[dict], cap: int):
        nonlocal used
        if not rows:
            return
        block = [f"## {section}"]
        for r in rows[:cap]:
            block.append(f"- {r['text']}  ⟨e:{r['eid'][:8]}⟩")
        block.append("")
        cost = estimate_tokens("\n".join(block))
        if used + cost <= budget_tokens:
            lines.extend(block)
            used += cost

    emit("決定事項 (Decisions)", sal["decision"], 12)
    emit("制約・前提 (Constraints)", sal["constraint"], 8)
    emit("未完了 (TODO)", sal["todo"], 10)
    emit("エラーと解決 (Errors → Fixes)", sal["error_fix"], 8)
    emit("触ったファイル (Artifacts)", sal["artifact"], 15)

    # 会話テール: 残り予算を TF-IDF + MMR で選抜（query空なら新しさ優先）
    convo = [e for e in events if e.get("actor") in ("user", "assistant") and not e.get("_deduped")]
    texts = [strip_noise(blocks_text(e.get("content", []))) for e in convo]
    keep = [(e, t) for e, t in zip(convo, texts) if t.strip()]
    if keep:
        convo, texts = map(list, zip(*keep))
        vecs = _tfidf_vectors(texts + ([query] if query else []))
        qv = vecs.pop() if query else None
        # 正規化(コサイン=単純内積に)して、選択のたびのノルム再計算を排除
        import math as _math

        def _norm(v: dict) -> dict:
            s = _math.sqrt(sum(x * x for x in v.values())) or 1.0
            return {k: x / s for k, x in v.items()}

        vecs = [_norm(v) for v in vecs]
        if qv:
            qv = _norm(qv)

        def _dot(a: dict, b: dict) -> float:
            if len(b) < len(a):
                a, b = b, a
            return sum(x * b.get(k, 0.0) for k, x in a.items())

        n = len(convo)
        base = [(0.55 * (i + 1) / n) + (0.45 * _dot(vecs[i], qv) if qv else 0.0) for i in range(n)]
        # 候補上限: 基礎スコア上位のみをMMR対象に（予算で選べる件数の数倍あれば十分。決定論）
        cand = sorted(range(n), key=lambda i: (-base[i], i))[:800]
        max_sim = {i: 0.0 for i in cand}  # 選択済み集合との最大類似（増分更新でO(n·k)）
        active = list(cand)
        selected: list[int] = []
        while active:
            best, best_s = None, -1.0
            for i in active:
                mmr = base[i] - 0.35 * max_sim[i]
                if mmr > best_s:
                    best, best_s = i, mmr
            item_text = truncate(texts[best], 1400)
            head = f"**{convo[best].get('actor')}** ({convo[best].get('source_kind')} {convo[best].get('ts') or ''}) ⟨e:{convo[best]['event_id'][:8]}⟩"
            cost = estimate_tokens(head + item_text) + 4
            if used + cost > budget_tokens:
                break
            selected.append(best)
            active.remove(best)
            used += cost
            bv = vecs[best]
            for i in active:  # 新規選択との類似だけを1回ずつ加味
                s = _dot(vecs[i], bv)
                if s > max_sim[i]:
                    max_sim[i] = s
        if selected:
            lines.append("## 直近の文脈 (Selected thread)")
            for i in sorted(selected):  # 時系列に戻す
                lines.append(f"**{convo[i].get('actor')}** ({convo[i].get('source_kind')} {convo[i].get('ts') or ''}) ⟨e:{convo[i]['event_id'][:8]}⟩")
                lines.append(truncate(texts[i], 1400))
                lines.append("")

    stats = {
        "budget_tokens": budget_tokens, "used_tokens": used,
        "input_events": len(events), **st1, **st2,
        "saved_chars_total": st1["saved_chars"] + st2["saved_chars"],
    }
    lines.append("---")
    lines.append(f"_tamo pack: {stats['input_events']} events → ~{used}/{budget_tokens} tok | "
                 f"dedup {st1['dup_events']}件 / snapshot折畳 {st2['snapshots_collapsed']}件 "
                 f"(約{stats['saved_chars_total']:,}字を圧縮) | ⟨e:xxxx⟩はイベントIDで原文追跡可_")
    return "\n".join(lines), stats
