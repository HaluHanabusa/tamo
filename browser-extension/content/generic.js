// tamo scoop — 汎用DOM抽出器（未知のAIチャットサイトの受け皿）
// ヒューリスティック3段:
//   1) role属性ヒント: [data-message-author-role] / [data-role] / [data-author]
//   2) クラス/属性名キーワード: user|human|you → user, assistant|ai|bot|model → assistant
//   3) どちらも無ければ「会話ターンらしい繰り返し要素」を user/assistant 交互とみなす
// 最後の砦: main全文を1メッセージとして送る（メタ情報は失わない）
(() => {
  const T = window.__tamo;

  function roleOf(el) {
    const attr =
      el.getAttribute("data-message-author-role") ||
      el.getAttribute("data-role") ||
      el.getAttribute("data-author") || "";
    const hint = (attr + " " + el.className).toLowerCase();
    if (/\b(user|human|you)\b/.test(hint)) return "user";
    if (/\b(assistant|ai|bot|model|agent)\b/.test(hint)) return "assistant";
    return null;
  }

  function turnCandidates() {
    let els = [...document.querySelectorAll("[data-message-author-role], [data-role], [data-author]")];
    if (els.length >= 2) return els;
    const root = document.querySelector("main") || document.body;
    els = [...root.querySelectorAll("article")];
    if (els.length >= 2) return els;
    // 同じ親の下に並ぶ「それなりの長さのテキストを持つ兄弟」を会話ターンとみなす
    const parents = new Map();
    for (const el of root.querySelectorAll("div, section, li")) {
      const t = (el.innerText || "").trim();
      if (t.length < 20 || el.children.length > 40) continue;
      const p = el.parentElement;
      if (!p) continue;
      parents.set(p, (parents.get(p) || 0) + 1);
    }
    let best = null, bestN = 0;
    for (const [p, n] of parents) if (n > bestN) { best = p; bestN = n; }
    return best && bestN >= 2 ? [...best.children].filter((c) => (c.innerText || "").trim().length >= 20) : [];
  }

  async function scoop() {
    const sc = await T.autoScrollLoad();  // 遅延読込の過去分を先に展開
    const els = turnCandidates();
    const msgs = [];
    if (els.length >= 2) {
      let alt = "user";
      for (const el of els) {
        const role = roleOf(el) || alt;
        alt = role === "user" ? "assistant" : "user";
        const text = (el.innerText || "").trim();
        const media = await T.mediaFrom(el, 2);
        const full = (text + media.notes).trim();
        if (full || media.attachments.length) {
          msgs.push({ role, text: full, ts: null, attachments: media.attachments });
        }
      }
    }
    if (!msgs.length) {
      const root = document.querySelector("main") || document.body;
      const text = (root.innerText || "").trim();
      if (!text) throw new Error("抽出できるテキストがありません");
      msgs.push({ role: "user", text: `[generic capture] ${text}`, ts: null, attachments: [] });
    }
    const sid = `${location.hostname}${location.pathname}`.replace(/[^\w./-]+/g, "_").slice(0, 80);
    const p = T.buildPayload(`web:${location.hostname}`, `web:${sid}`, document.title, msgs);
    if (sc.steps) p.note = ((p.note || "") + ` dom-scroll ${sc.steps}steps ${sc.before}->${sc.after}px`).trim();
    return p;
  }

  window.__tamoSites = window.__tamoSites || {};
  window.__tamoSites.__generic = scoop;
})();
