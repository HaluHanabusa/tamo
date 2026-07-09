// tamo scoop — ChatGPT (chatgpt.com / chat.openai.com) アダプタ
// ページ内Cookieで /api/auth/session からアクセストークンを取り、
// backend-api/conversation/<id> の mapping ツリーを current_node から根へ遡って復元する。
(() => {
  const T = window.__tamo;

  function convId() {
    const m = location.pathname.match(/\/c\/([0-9a-f-]{10,})/i);
    return m ? m[1] : null;
  }

  async function accessToken() {
    const res = await fetch("/api/auth/session", { credentials: "include" });
    if (!res.ok) throw new Error(`session ${res.status}`);
    const j = await res.json();
    if (!j.accessToken) throw new Error("未ログイン？accessTokenなし");
    return j.accessToken;
  }

  async function fileToAttachment(fileId, name, headers) {
    try {
      const meta = await fetch(`/backend-api/files/${fileId}/download`, { headers, credentials: "include" });
      if (!meta.ok) return null;
      const j = await meta.json();
      if (!j.download_url) return null;
      return await T.fetchAsAttachment(j.download_url, name, null);
    } catch (_e) {
      return null;
    }
  }

  async function mapNode(node, headers) {
    const msg = node.message;
    const role = msg.author && msg.author.role;
    if (role !== "user" && role !== "assistant") return null;
    let text = "";
    const atts = [];
    const c = msg.content || {};
    const parts = c.parts || [];
    for (const p of parts) {
      if (typeof p === "string") text += p + "\n";
      else if (p && p.content_type === "image_asset_pointer" && p.asset_pointer) {
        const fileId = String(p.asset_pointer).split("://").pop();
        const att = await fileToAttachment(fileId, `${fileId}.png`, headers);
        if (att) atts.push(att);
        else text += T.skippedNote(fileId, "image", p.size_bytes);
      }
    }
    if (c.content_type === "code" && c.text) text += "```\n" + c.text + "\n```\n";
    if (!text && c.text) text += c.text;
    // ユーザーがアップロードしたファイルのメタ（PDF等）。bytesはdownloadで取れれば同梱
    for (const a of (msg.metadata && msg.metadata.attachments) || []) {
      const att = a.id ? await fileToAttachment(a.id, a.name, headers) : null;
      if (att) atts.push(att);
      else text += T.skippedNote(a.name, a.mime_type || a.mimeType, a.size);
    }
    if (!text.trim() && !atts.length) return null;
    const ts = msg.create_time ? new Date(msg.create_time * 1000).toISOString() : null;
    return { role, text: text.trim(), ts, attachments: atts };
  }

  async function scoop() {
    const id = convId();
    if (!id) throw new Error("会話URLではありません（/c/... を開いてください）");
    const token = await accessToken();
    const headers = { Authorization: `Bearer ${token}` };
    const res = await fetch(`/backend-api/conversation/${id}`, { headers, credentials: "include" });
    if (!res.ok) throw new Error(`conversation ${res.status}`);
    const data = await res.json();
    const mapping = data.mapping || {};
    // current_node から根へ遡り、逆順にする
    const chain = [];
    let cur = data.current_node;
    let guard = 0;
    while (cur && mapping[cur] && guard++ < 5000) {
      chain.push(mapping[cur]);
      cur = mapping[cur].parent;
    }
    chain.reverse();
    const msgs = [];
    for (const node of chain) {
      if (!node.message) continue;
      const m = await mapNode(node, headers);
      if (m) msgs.push(m);
    }
    if (!msgs.length) throw new Error("メッセージ復元に失敗（mapping形式が変わった可能性）");
    return T.buildPayload("chatgpt_web", `chatgpt:${id}`, data.title, msgs);
  }

  window.__tamoSites = window.__tamoSites || {};
  window.__tamoSites["chatgpt.com"] = scoop;
  window.__tamoSites["chat.openai.com"] = scoop;
})();
