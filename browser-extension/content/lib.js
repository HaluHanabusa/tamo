// tamo scoop — 共有ヘルパ（window.__tamo に集約）
// 添付の方針:
//   1. バイト列が安く取れるもの（画像・ダウンロードURLのあるファイル）は b64 で同梱
//   2. プラットフォームが抽出済みテキストを持つもの（claude.ai の extracted_content）はそれを同梱
//   3. 取れないものは「名前とメタ情報だけは必ず本文に残す」 — 情報を黙って落とさない
// サイズ上限を超えたものも 3 に落とす。tamo側でCAS格納+テキスト抽出されFTS検索可能になる。
(() => {
  const LIMITS = {
    perAttachment: 6 * 1024 * 1024,   // 生バイト6MB/添付
    total: 20 * 1024 * 1024,          // 会話合計20MB
    count: 20,                        // 添付数
    perMessageChars: 100_000,
    messages: 400,
  };

  const state = { totalBytes: 0, count: 0 };

  function resetBudget() { state.totalBytes = 0; state.count = 0; }

  function nowIso() { return new Date().toISOString(); }

  function textToB64(s) {
    // UTF-8安全なbase64
    const bytes = new TextEncoder().encode(s);
    let bin = "";
    for (let i = 0; i < bytes.length; i += 0x8000) {
      bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
    }
    return btoa(bin);
  }

  async function blobToB64(blob) {
    const buf = new Uint8Array(await blob.arrayBuffer());
    let bin = "";
    for (let i = 0; i < buf.length; i += 0x8000) {
      bin += String.fromCharCode.apply(null, buf.subarray(i, i + 0x8000));
    }
    return btoa(bin);
  }

  // URLからb64添付を作る（上限超過・失敗は null）
  async function fetchAsAttachment(url, name, mimeHint) {
    if (state.count >= LIMITS.count) return null;
    try {
      const res = await fetch(url, { credentials: "include" });
      if (!res.ok) return null;
      const blob = await res.blob();
      if (blob.size > LIMITS.perAttachment) return null;
      if (state.totalBytes + blob.size > LIMITS.total) return null;
      state.totalBytes += blob.size;
      state.count += 1;
      return {
        name: name || url.split("/").pop().split("?")[0] || "attachment",
        mime: mimeHint || blob.type || "application/octet-stream",
        data_b64: await blobToB64(blob),
      };
    } catch (_e) {
      return null;
    }
  }

  function textAttachment(name, text) {
    if (!text || state.count >= LIMITS.count) return null;
    state.count += 1;
    return { name, mime: "text/plain", data_b64: textToB64(text.slice(0, 500_000)) };
  }

  // 取得できなかった添付は本文にメタ情報として必ず残す（種別がわかれば文脈は伝わる）
  function kindJa(mime, name) {
    const m = (mime || "").toLowerCase();
    const n = (name || "").toLowerCase();
    if (m.startsWith("image/")) return "画像";
    if (m.startsWith("video/") || /\.(mp4|mov|webm|avi|mkv)$/.test(n)) return "動画";
    if (m.startsWith("audio/") || /\.(mp3|wav|ogg|flac|m4a)$/.test(n)) return "音声";
    if (m === "application/pdf" || n.endsWith(".pdf")) return "PDF";
    if (m.includes("wordprocessingml") || /\.docx?$/.test(n)) return "Word文書";
    if (m.includes("spreadsheetml") || /\.(xlsx?|csv)$/.test(n)) return "表計算";
    if (m.includes("presentationml") || /\.pptx?$/.test(n)) return "スライド";
    if (m.startsWith("text/")) return "テキスト";
    return "ファイル";
  }

  function skippedNote(name, mime, size) {
    const kb = size ? ` ${Math.round(size / 1024)}KB` : "";
    return `\n[添付(${kindJa(mime, name)} 未取得) ${name || "?"} ${mime || ""}${kb}]`;
  }

  function clipText(t) { return (t || "").slice(0, LIMITS.perMessageChars); }

  function buildPayload(source, session, title, messages) {
    const kept = messages.slice(-LIMITS.messages);
    const dropped = messages.length - kept.length;
    const clipped = kept.filter((m) => (m.text || "").length > LIMITS.perMessageChars).length;
    const payload = {
      schema: "tamo.inbox.v1",
      source,
      session,
      title: title || document.title || null,
      captured_at: nowIso(),
      url: location.href,
      messages: kept.map((m) => ({
        role: m.role || "user",
        text: clipText(m.text),
        ts: m.ts || null,
        attachments: (m.attachments || []).filter(Boolean),
      })),
    };
    if (dropped || clipped) {
      // 黙って落とさない: 何をどれだけ切ったかをnoteに残す（tamo側はmetaイベントとして保全し、UIにも出る）
      const parts = [];
      if (dropped) parts.push(`古い${dropped}件を上限${LIMITS.messages}件で省略`);
      if (clipped) parts.push(`${clipped}件を${LIMITS.perMessageChars}字で切詰め`);
      payload.note = `[上限切詰め] ${parts.join(" / ")}`;
    }
    return payload;
  }

  // メッセージ要素内のメディアを柔軟に回収する:
  //   画像 → b64同梱 / 動画・音声 → ポスター画像があれば同梱し、本体はメタノート
  // 返り値 {attachments, notes} — notes は呼び出し側で本文に連結する
  async function mediaFrom(el, maxImages = 4) {
    const attachments = [];
    let notes = "";
    for (const img of el.querySelectorAll("img")) {
      if (attachments.length >= maxImages) break;
      const src = img.currentSrc || img.src;
      if (!src || src.startsWith("data:image/svg")) continue;
      if (img.naturalWidth && img.naturalWidth < 48) continue; // アイコン除外
      const att = await fetchAsAttachment(src, null, null);
      if (att) attachments.push(att);
    }
    for (const v of el.querySelectorAll("video")) {
      const src = v.currentSrc || v.src || (v.querySelector("source") && v.querySelector("source").src) || "";
      if (v.poster) {
        const att = await fetchAsAttachment(v.poster, "video_poster.jpg", null);
        if (att) attachments.push(att);
      }
      notes += skippedNote(src ? src.split("/").pop().split("?")[0] : "embedded", "video/*", null);
    }
    for (const a of el.querySelectorAll("audio")) {
      const src = a.currentSrc || a.src || "";
      notes += skippedNote(src ? src.split("/").pop().split("?")[0] : "embedded", "audio/*", null);
    }
    return { attachments, notes };
  }

  async function imagesFrom(el, max = 4) {  // 後方互換
    return (await mediaFrom(el, max)).attachments;
  }

  // 遅延読込UI対策: 会話コンテナを最上部までスクロールして過去分を展開してから抽出する。
  // 「スクロール高がこれ以上伸びない」= 先頭まで読込済み、で打ち切る（決定論的な終了条件）。
  async function autoScrollLoad(maxSteps = 25, settleMs = 700) {
    const cands = [document.scrollingElement,
                   ...document.querySelectorAll("main, main div, main section")]
      .filter((el) => el && el.scrollHeight > el.clientHeight + 200);
    const sc = cands.sort((a, b) => b.scrollHeight - a.scrollHeight)[0];  // 最大の可動域=会話リスト
    if (!sc) return { steps: 0, before: 0, after: 0 };
    const before = sc.scrollHeight;
    const origTop = sc.scrollTop;
    let last = -1, steps = 0;
    for (; steps < maxSteps; steps++) {
      // 進捗フック（fab等が「n/25」を表示する。最大~17秒の無言フリーズに見せない）
      if (typeof window.__tamo.onScrollStep === "function") {
        try { window.__tamo.onScrollStep(steps + 1, maxSteps); } catch (_e) { /* 表示は本処理を妨げない */ }
      }
      sc.scrollTop = 0;
      await new Promise((r) => setTimeout(r, settleMs));
      if (sc.scrollHeight === last) break;
      last = sc.scrollHeight;
    }
    const after = sc.scrollHeight;
    sc.scrollTop = origTop + (after - before);  // 元の見た目位置へ概ね復帰
    return { steps, before, after };
  }

  // 拡張の更新(リロード)後、更新前から開いていたタブの旧content scriptは
  // chrome.runtime を失う(孤児化)。その検知用。
  function alive() {
    try {
      return !!(chrome && chrome.runtime && chrome.runtime.id);
    } catch (_e) {
      return false;
    }
  }

  window.__tamo = {
    LIMITS, resetBudget, nowIso, textToB64, blobToB64, alive,
    fetchAsAttachment, textAttachment, kindJa, skippedNote, clipText,
    buildPayload, mediaFrom, imagesFrom, autoScrollLoad,
    onScrollStep: null,  // (step, maxSteps) => void — 自動スクロール中の進捗表示用フック
  };
})();
