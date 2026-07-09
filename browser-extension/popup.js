// tamo scoop — popup
const $ = (id) => document.getElementById(id);
const say = (t) => { $("status").textContent = t; };

async function activeTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  return tab;
}

async function scoopViaContentScript(tabId) {
  return await chrome.tabs.sendMessage(tabId, { type: "tamo.scoop" });
}

async function scoopViaInjection(tabId) {
  // manifest対象外のサイト: activeTab権限でその場注入 → 汎用抽出
  await chrome.scripting.executeScript({
    target: { tabId },
    files: ["content/lib.js", "content/generic.js"],
  });
  const [res] = await chrome.scripting.executeScript({
    target: { tabId },
    func: async () => {
      window.__tamo.resetBudget();
      try {
        const payload = await window.__tamoSites.__generic();
        return { ok: true, payload, adapter: "generic(injected)" };
      } catch (e) {
        return { ok: false, error: e.message };
      }
    },
  });
  return res.result;
}

$("save").onclick = async () => {
  await chrome.storage.sync.set({ port: Number($("port").value) || 8787, token: $("token").value.trim() });
  say("保存しました");
};

$("health").onclick = async () => {
  const r = await chrome.runtime.sendMessage({ type: "tamo.health" });
  say(r.ok ? "tamoに接続OK" : `接続NG: ${r.error || r.status}\n（WSL側で tamo watch --http を起動してください）`);
};

$("recall").onclick = async () => {
  const q = $("rq").value.trim();
  if (!q) return say("検索語を入れてください");
  say("tamoから過去文脈を取得中…");
  const r = await chrome.runtime.sendMessage({ type: "tamo.recall", query: q, source: $("rsrc").value });
  if (!r.ok) return say(`取得失敗: ${r.error}`);
  await navigator.clipboard.writeText(r.text);
  say(`コピーしました ✓ (${r.text.length}字)\nそのままChatGPT/Gemini/Claudeの入力欄に貼れます`);
};

$("rq").onkeydown = (e) => { if (e.key === "Enter") $("recall").click(); };

$("scoop").onclick = async () => {
  say("掬っています…");
  const tab = await activeTab();
  let res;
  try {
    res = await scoopViaContentScript(tab.id);
  } catch (_e) {
    res = await scoopViaInjection(tab.id); // content script未注入のサイト
  }
  if (!res || !res.ok) return say(`抽出失敗: ${res && res.error}`);
  const p = res.payload;
  const nAtt = p.messages.reduce((n, m) => n + (m.attachments || []).length, 0);
  say(`抽出OK (${res.adapter}${res.warn ? " ⚠" + res.warn : ""})\n` +
      `messages=${p.messages.length} attachments=${nAtt}\n投函中…`);
  const post = await chrome.runtime.sendMessage({ type: "tamo.post", payload: p });
  if (post.ok) say(`投函完了 ✓ (${p.session})\nmessages=${p.messages.length} attachments=${nAtt}\n` +
                   "tamo collect（またはwatch）で取込まれます");
  else say(`投函失敗: ${post.error || "HTTP " + post.status}`);
};

$("fab").onchange = async () => {
  await chrome.storage.sync.set({ fab: $("fab").checked });
  say($("fab").checked ? "対応サイトに🎣ボタンを常時表示します（タブ再読込後に反映）" : "🎣ボタンを非表示にしました");
};

chrome.storage.sync.get({ port: 8787, token: "", fab: true }).then(async (d) => {
  $("port").value = d.port;
  $("token").value = d.token;
  $("fab").checked = d.fab;
  if (!d.token) {  // 初回は自動ペアリング（手貼り不要に）
    const r = await chrome.runtime.sendMessage({ type: "tamo.pair" });
    if (r && r.ok) {
      const s = await chrome.storage.sync.get({ token: "" });
      $("token").value = s.token;
      say("tamoと自動ペアリングしました ✓（トークン設定済み）");
    } else {
      say("自動ペアリング失敗: tamo serve を起動してから開き直すか、tamo token の値を手動で貼ってください");
    }
  }
});
