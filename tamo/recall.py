"""tamo.recall — 「あの件どうなってた？」への一発回答素材（セッション単位ダイジェスト）。

v2設計: recallの単位は「イベントの前後」ではなく **ヒットしたセッション** に揃える。
ただし生の全文は流さない（平均200イベント/セッションの実DBでは4ヒット≈19万字となり
受け手の文脈を圧迫する）。代わりに各セッションを3部構成の決定論ダイジェストにする:

  1. ★アンカー   … 一致箇所とその直前直後（何が話されたかの現場）+ 添付内の根拠抜粋
  2. 要点走査     … セッション**全体**をP3規則で走査した 決定/エラー→解決/TODO/制約
                    （一致から遠く離れた顛末・結論をここで確実に拾う）
  3. 終盤         … 最後の数発言（そのセッションが「どう終わったか」）

  小さいセッション(≤SMALL件)は分割せず全文を載せる。
  さらに深掘りしたい場合の get_session / get_blob_text 導線を末尾に付す。
"""
from __future__ import annotations

from .optimize import extract_salience
from .schema import blocks_text
from .store import Store
from .util import estimate_tokens, strip_noise, truncate

SMALL = 12  # これ以下のセッションは要約せず全文


def _slim(e: dict, mark: str = "・") -> str:
    text = truncate(strip_noise(blocks_text(e.get("content", []))), 260)
    kind = f"({e['kind']}) " if e.get("kind") not in (None, "message") else ""
    return f"{mark} {e.get('actor')}: {kind}{text}  ⟨e:{e['event_id'][:8]}⟩"


def _attachment_evidence(store: Store, event_id: str, terms: list[str]) -> list[str]:
    out: list[str] = []
    rows = store.con.execute(
        "SELECT b.sha256, b.name, t.text FROM blob_refs r"
        " JOIN blobs b ON b.sha256 = r.sha256"
        " LEFT JOIN blob_texts t ON t.sha256 = b.sha256"
        " WHERE r.event_id = ?", (event_id,)).fetchall()
    for sha, name, text in rows:
        if not text:
            continue
        pos = -1
        for term in terms:
            pos = text.find(term)
            if pos >= 0:
                break
        if pos < 0:
            continue
        s, e = max(0, pos - 100), min(len(text), pos + 180)
        excerpt = ("…" if s else "") + text[s:e].replace("\n", " ") + ("…" if e < len(text) else "")
        out.append(f"    └ 📎 {name or sha[:12]}: {excerpt}  (sha={sha[:12]})")
    return out


def _session_digest(store: Store, hit: dict, terms: list[str], label: str = "") -> list[str]:
    sk = hit["session_key"]
    evs = store.iter_session_events(sk)
    total = len(evs)
    row = store.con.execute(
        "SELECT title, first_ts, last_ts FROM sessions WHERE session_key=?", (sk,)).fetchone()
    title, first_ts, last_ts = row if row else (None, None, None)
    head = f"## {label}{sk}"
    if title:
        head += f" — {title}"
    block = [head, f"({first_ts or '-'} → {last_ts or '-'} / 全{total}イベント)"]

    hidx = next((i for i, e in enumerate(evs) if e["event_id"] == hit["event_id"]), None)
    shown: set[str] = set()

    if total <= SMALL:  # 小さいセッションは素直に全文
        for i, e in enumerate(evs):
            line = _slim(e, "★" if i == hidx else "・")
            if not line.split(": ", 1)[-1].strip():
                continue
            block.append(line)
            if i == hidx:
                block.extend(_attachment_evidence(store, e["event_id"], terms))
        block.append("")
        return block

    # 1. ★アンカー（一致±少数）
    if hidx is not None:
        block.append("### ★一致箇所")
        for i in range(max(0, hidx - 2), min(total, hidx + 3)):
            block.append(_slim(evs[i], "★" if i == hidx else "・"))
            shown.add(evs[i]["event_id"])
            if i == hidx:
                block.extend(_attachment_evidence(store, evs[i]["event_id"], terms))

    # 2. セッション全体の要点走査（一致から遠い顛末・結論をここで拾う）
    sal = extract_salience(evs)
    sections = [("decision", "決定"), ("error_fix", "エラー→解決"), ("todo", "TODO"), ("constraint", "制約")]
    sal_lines = []
    for key, label in sections:
        for r in sal.get(key, [])[: 3 if key == "decision" else 2]:
            txt = r["text"]
            if "→ FIX:" in txt:  # エラー行自体に解決が書かれている場合のERR/FIX同文重複を畳む
                err, fix = (s.strip() for s in txt.split("→ FIX:", 1))
                if err.replace("ERR:", "").strip() == fix.strip():
                    txt = fix
            sal_lines.append(f"- [{label}] {truncate(txt, 200)}  ⟨e:{r['eid'][:8]}⟩")
    if sal_lines:
        block.append("### セッション全体の要点（全イベント走査）")
        block.extend(sal_lines)

    # 3. 終盤（どう終わったか）
    tail = [e for e in evs[-2:] if e["event_id"] not in shown]
    tail = [e for e in tail if strip_noise(blocks_text(e.get("content", []))).strip()]
    if tail:
        block.append("### 終盤（どう終わったか）")
        block.extend(_slim(e) for e in tail)

    block.append("")
    return block


def recall(store: Store, query: str, budget_tokens: int = 3500, max_hits: int = 3,
           before: int = 2, after: int = 4, source: str | None = None) -> str:
    """query一発で、ヒットした各セッションの決定論ダイジェストをMarkdown合成する。
    source: 面の絞り込み（"gemini"/"chatgpt"/"claude_web"/"claude_code"等の部分一致）。"""
    terms = [t for t in query.split() if t]
    raw_hits = store.search(query, limit=max_hits * 3, source=source)
    seen: set[str] = set()
    hits = []
    for h in raw_hits:
        if h["session_key"] in seen:
            continue
        seen.add(h["session_key"])
        hits.append(h)
        if len(hits) >= max_hits:
            break
    if not hits:
        return f"# tamo recall: {query}\n\n(該当なし — 別の語で `recall` するか `search_context` を試してください)\n"

    # 複数セッションに跨る話題は「最新が現在の状態」— 最新優先で提示し、予算も最新に優先配分する
    def _last_ts(sk: str) -> str:
        row = store.con.execute("SELECT last_ts FROM sessions WHERE session_key=?", (sk,)).fetchone()
        return (row[0] if row and row[0] else "") or ""

    hits.sort(key=lambda h: _last_ts(h["session_key"]), reverse=True)

    src_tag = f'（source~"{source}"）' if source else ""
    lines = [f"# tamo recall: {query}{src_tag}", ""]
    if len(hits) >= 2:  # 変遷の時系列サマリ（読み手が evolution を一目で掴めるように）
        days = [(_last_ts(h["session_key"])[:10] or "?") for h in hits]
        lines.append(f"🕒 この話題は{len(hits)}セッションに跨る: " + " → ".join(reversed(days))
                     + "（以下は新しい順 = ①が現在の状態）")
        lines.append("")
    used = estimate_tokens("\n".join(lines))
    shown = 0
    marks = ["① ", "② ", "③ ", "④ "]
    for n, h in enumerate(hits):
        label = (marks[n] if n < len(marks) else f"{n+1}. ") + ("(最新) " if n == 0 and len(hits) >= 2 else "")
        block = _session_digest(store, h, terms, label=label)
        cost = estimate_tokens("\n".join(block))
        if used + cost > budget_tokens and shown:
            break
        lines.extend(block)
        used += cost
        shown += 1
    lines.append(f"---\n_★=一致行 📎=添付根拠 / セッション全文: get_session(session_key)、"
                 f"続きだけ: since_event_id=\"⟨e:...⟩の8桁\"、添付全文: get_blob_text(sha) — "
                 f"{shown}/{len(hits)}セッション ~{used}tok_")
    return "\n".join(lines)
