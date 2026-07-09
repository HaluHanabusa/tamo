// tamo scoop — background service worker
// 役割はひとつ: content script が組んだ tamo.inbox.v1 payload を
// http://127.0.0.1:<port>/inbox へ X-Tamo-Token 付きで POST するだけ。
// （ページのCSP/mixed-contentの影響を受けないよう、localhost送信はここで行う）

async function settings() {
  const d = await chrome.storage.sync.get({ port: 8787, token: "" });
  return d;
}

const t = (key, subs) => {
  try {
    return chrome.i18n.getMessage(key, subs) || key;
  } catch (_e) {
    return key;
  }
};

// サーバのエラー本文（"bad token: 再ペアリングで解消…" 等の対処ガイド）を捨てずに届ける
async function errorDetail(res) {
  let body = "";
  try { body = (await res.text()).trim().slice(0, 300); } catch (_e) { /* 本文なし */ }
  return `HTTP ${res.status}${body ? ` — ${body}` : ""}`;
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
  if (!token) return { ok: false, error: t("bgPairFail") };
  try {
    const res = await fetch(`http://127.0.0.1:${port}/inbox`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Tamo-Token": token },
      body: JSON.stringify(payload),
    });
    if (res.status === 204) return { ok: true, status: 204 };
    return { ok: false, status: res.status, error: await errorDetail(res) };
  } catch (e) {
    return { ok: false, error: t("bgConnectFail", [e.message]) };
  }
}

async function health() {
  const { port, token } = await settings();
  try {
    // トークンを持っていれば一緒に送って認可まで確認する
    // （到達性だけの確認だと、トークン不一致でも「接続OK」と出て直後の投函403で裏切られる）
    const res = await fetch(`http://127.0.0.1:${port}/health`,
      token ? { headers: { "X-Tamo-Token": token } } : {});
    if (res.ok) return { ok: true, status: res.status };
    return { ok: false, status: res.status, tokenBad: res.status === 403, error: await errorDetail(res) };
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
      if (!token) return sendResponse({ ok: false, error: t("bgNotPaired") });
      try {
        const src = msg.source ? `&source=${encodeURIComponent(msg.source)}` : "";
        const res = await fetch(`http://127.0.0.1:${port}/recall?q=${encodeURIComponent(msg.query)}${src}`,
          { headers: { "X-Tamo-Token": token } });
        if (!res.ok) return sendResponse({ ok: false, error: await errorDetail(res) });
        sendResponse({ ok: true, text: await res.text() });
      } catch (e) {
        sendResponse({ ok: false, error: e.message });
      }
    })();
    return true;
  }
  return false;
});
