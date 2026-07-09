// tamo scoop — background service worker
// 役割はひとつ: content script が組んだ tamo.inbox.v1 payload を
// http://127.0.0.1:<port>/inbox へ X-Tamo-Token 付きで POST するだけ。
// （ページのCSP/mixed-contentの影響を受けないよう、localhost送信はここで行う）

async function settings() {
  const d = await chrome.storage.sync.get({ port: 8787, token: "" });
  return d;
}

async function pair() {
  const { port } = await settings();
  try {
    const res = await fetch(`http://127.0.0.1:${port}/pair`);
    if (!res.ok) return null;
    const token = (await res.text()).trim();
    if (token) await chrome.storage.sync.set({ token });
    return token || null;
  } catch (_e) {
    return null;
  }
}

async function postInbox(payload) {
  const { port } = await settings();
  let { token } = await settings();
  if (!token) token = await pair();  // 未設定なら自動ペアリング（GET /pair, localhost限定）
  if (!token) return { ok: false, error: "tamoとペアリングできません（tamo serve は起動していますか）" };
  try {
    const res = await fetch(`http://127.0.0.1:${port}/inbox`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Tamo-Token": token },
      body: JSON.stringify(payload),
    });
    return { ok: res.status === 204, status: res.status };
  } catch (e) {
    return { ok: false, error: `tamoに接続できません: ${e.message}（tamo watch --http は起動していますか）` };
  }
}

async function health() {
  const { port } = await settings();
  try {
    const res = await fetch(`http://127.0.0.1:${port}/health`);
    return { ok: res.ok, status: res.status };
  } catch (e) {
    return { ok: false, error: e.message };
  }
}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg && msg.type === "tamo.post") {
    postInbox(msg.payload).then(sendResponse);
    return true; // async
  }
  if (msg && msg.type === "tamo.health") {
    health().then(sendResponse);
    return true;
  }
  if (msg && msg.type === "tamo.pair") {
    pair().then((t) => sendResponse({ ok: !!t }));
    return true;
  }
  if (msg && msg.type === "tamo.recall") {
    (async () => {
      const { port } = await settings();
      let { token } = await settings();
      if (!token) token = await pair();
      if (!token) return sendResponse({ ok: false, error: "ペアリング未完了（tamo serve 起動中に開き直してください）" });
      try {
        const src = msg.source ? `&source=${encodeURIComponent(msg.source)}` : "";
        const res = await fetch(`http://127.0.0.1:${port}/recall?q=${encodeURIComponent(msg.query)}${src}`,
          { headers: { "X-Tamo-Token": token } });
        if (!res.ok) return sendResponse({ ok: false, error: `HTTP ${res.status}` });
        sendResponse({ ok: true, text: await res.text() });
      } catch (e) {
        sendResponse({ ok: false, error: e.message });
      }
    })();
    return true;
  }
  return false;
});
