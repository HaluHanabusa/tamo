// tamo scoop — claude.ai アダプタ
// DOMスクレイピングではなく、ページと同じCookieで同一オリジンAPIを叩いて会話JSONを取る。
// （DOMはUI改版のたびに壊れるが、アプリ自身が使うAPIはずっと安定している）
// それでも非公開APIなので、形が変わったら generic DOM にフォールバックする。
(() => {
  const T = window.__tamo;

  async function getOrgId() {
    const res = await fetch("/api/organizations", { credentials: "include" });
    if (!res.ok) throw new Error(`organizations ${res.status}`);
    const orgs = await res.json();
    const org = orgs.find((o) => (o.capabilities || []).includes("chat")) || orgs[0];
    if (!org) throw new Error("no org");
    return org.uuid;
  }

  function convId() {
    const m = location.pathname.match(/\/chat\/([0-9a-f-]{30,})/i);
    return m ? m[1] : null;
  }

  async function mapMessage(org, msg) {
    const role = msg.sender === "human" ? "user" : "assistant";
    let text = "";
    for (const c of msg.content || []) {
      if (c.type === "text" && c.text) text += c.text + "\n";
      else if (c.type === "tool_use") text += `[tool_use ${c.name || ""}]\n`;
      else if (c.type === "tool_result") text += `[tool_result]\n`;
    }
    if (!text && msg.text) text = msg.text; // 旧形式
    const atts = [];
    // 1) アップロード文書: claude.ai が抽出済みテキストを持っている → それを添付にする
    for (const a of msg.attachments || []) {
      if (a.extracted_content) {
        const att = T.textAttachment(`${a.file_name || "doc"}.extracted.txt`, a.extracted_content);
        if (att) atts.push(att);
        else text += T.skippedNote(a.file_name, a.file_type, a.file_size);
      } else {
        text += T.skippedNote(a.file_name, a.file_type, a.file_size);
      }
    }
    // 2) 画像などのファイル: preview/thumbnail URL をベストエフォートで取得
    for (const f of msg.files || []) {
      const url =
        f.preview_url || f.thumbnail_url ||
        (f.file_uuid ? `/api/organizations/${org}/files/${f.file_uuid}/preview` : null);
      const att = url ? await T.fetchAsAttachment(url, f.file_name, null) : null;
      if (att) atts.push(att);
      else text += T.skippedNote(f.file_name, f.file_kind, f.file_size_bytes);
    }
    return { role, text: text.trim(), ts: msg.created_at || null, attachments: atts };
  }

  async function scoop() {
    const id = convId();
    if (!id) throw new Error("会話URLではありません（/chat/... を開いてください）");
    const org = await getOrgId();
    const res = await fetch(
      `/api/organizations/${org}/chat_conversations/${id}?tree=True&rendering_mode=messages&render_all_tools=true`,
      { credentials: "include" },
    );
    if (!res.ok) throw new Error(`conversation ${res.status}`);
    const data = await res.json();
    const msgs = [];
    for (const m of data.chat_messages || []) msgs.push(await mapMessage(org, m));
    if (!msgs.length) throw new Error("chat_messagesが空（API形式が変わった可能性）");
    return T.buildPayload("claude_web", `claude:${id}`, data.name, msgs);
  }

  window.__tamoSites = window.__tamoSites || {};
  window.__tamoSites["claude.ai"] = scoop;
})();
