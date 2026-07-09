// tamo scoop — Gemini (gemini.google.com) アダプタ
// Geminiは会話取得に使える安定した同一オリジンJSON APIが無いため、
// カスタム要素 <user-query> / <model-response> を読むDOM方式（改版で壊れうる前提）。
(() => {
  const T = window.__tamo;

  async function scoop() {
    const sc = await T.autoScrollLoad();  // 遅延読込の過去分を先に展開
    const nodes = document.querySelectorAll("user-query, model-response");
    if (!nodes.length) throw new Error("user-query/model-response が見つからない（DOM改版の可能性）");
    const msgs = [];
    for (const n of nodes) {
      const role = n.tagName.toLowerCase() === "user-query" ? "user" : "assistant";
      const text = (n.innerText || "").trim();
      const media = await T.mediaFrom(n, 3);
      const full = (text + media.notes).trim();
      if (full || media.attachments.length) {
        msgs.push({ role, text: full, ts: null, attachments: media.attachments });
      }
    }
    const id = (location.pathname.match(/\/app\/([0-9a-f]+)/i) || [])[1] || String(Date.now());
    const p = T.buildPayload("gemini_web", `gemini:${id}`, document.title, msgs);
    if (sc.steps) p.note = `dom-scroll ${sc.steps}steps ${sc.before}->${sc.after}px`;
    return p;
  }

  window.__tamoSites = window.__tamoSites || {};
  window.__tamoSites["gemini.google.com"] = scoop;
})();
