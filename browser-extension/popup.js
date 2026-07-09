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
  say("保存しました。「接続確認」で疎通とトークンを確かめられます");
};

$("pair").onclick = async () => {
  // トークン不一致(403)からの復帰導線。/pair はlocalhost限定・サーバの現行トークンを返す
  say("ペアリング中…");
  const r = await chrome.runtime.sendMessage({ type: "tamo.pair" });
  if (r && r.ok) {
    const s = await chrome.storage.sync.get({ token: "" });
    $("token").value = s.token;
    say("再ペアリング完了 ✓（トークンを更新しました）");
  } else {
    say("ペアリング失敗: tamo serve が起動しているか、ポート番号が合っているか確認してください");
  }
};

$("health").onclick = async () => {
  say("接続確認中…");
  const r = await chrome.runtime.sendMessage({ type: "tamo.health" });
  if (r.ok) {
    say($("token").value.trim()
      ? "tamoに接続OK ✓（トークンも一致）"
      : "tamoに接続OK ✓（トークン未設定 — 初回投函時に自動ペアリングします）");
    return;
  }
  if (r.tokenBad) return say("サーバには届きましたがトークンが不一致です\n→「再ペアリング」を押すと解消します");
  say(`接続NG: ${r.error || r.status}\n（tamo serve を起動してください。ポート設定も確認: 既定8787）`);
};

$("recall").onclick = async () => {
  const q = $("rq").value.trim();
  if (!q) return say("検索語を入れてください");
  say("tamoから過去文脈を取得中…");
  const r = await chrome.runtime.sendMessage({ type: "tamo.recall", query: q, source: $("rsrc").value });
  if (!r.ok) return say(`取得失敗: ${r.error}`);
  if (r.text.includes("(該当なし")) {  // 空振りを「コピー成功」と偽らない
    return say(`該当なし: ${q}\n別の語で試すか、対象の会話を先に「掬う」で取り込んでください`);
  }
  await navigator.clipboard.writeText(r.text);
  say(`コピーしました ✓ (${r.text.length}字)\nそのままChatGPT/Gemini/Claudeの入力欄に貼れます`);
};

$("rq").onkeydown = (e) => { if (e.key === "Enter") $("recall").click(); };

$("scoop").onclick = async () => {
  say("掬っています…（長い会話は過去分の読み込みに十数秒かかることがあります）");
  let res;
  try {
    const tab = await activeTab();
    try {
      res = await scoopViaContentScript(tab.id);
    } catch (_e) {
      res = await scoopViaInjection(tab.id); // content script未注入のサイト
    }
  } catch (e) {
    // chrome:// やウェブストア等、拡張が触れないページでは executeScript が拒否される。
    // ここを未処理にすると「掬っています…」のまま永久フリーズする
    return say(`このページでは掬えません（${e.message}）\nchrome:// 等の保護ページやPDFビューアは対象外です`);
  }
  if (!res || !res.ok) return say(`抽出失敗: ${(res && res.error) || "不明なエラー"}`);
  const p = res.payload;
  const nAtt = p.messages.reduce((n, m) => n + (m.attachments || []).length, 0);
  say(`抽出OK (${res.adapter}${res.warn ? " ⚠" + res.warn : ""})\n` +
      `messages=${p.messages.length} attachments=${nAtt}\n投函中…`);
  const post = await chrome.runtime.sendMessage({ type: "tamo.post", payload: p });
  if (post.ok) {
    say(`投函完了 ✓ (${p.session})\nmessages=${p.messages.length} attachments=${nAtt}` +
        (p.note ? `\n⚠ ${p.note}` : "") +
        "\ntamo serve が次のポーリングで自動取込します");
  } else {
    say(`投函失敗: ${post.error || "HTTP " + post.status}` +
        (post.status === 403 ? "\n→「再ペアリング」を押すと解消します" : ""));
  }
};

$("fab").onchange = async () => {
  await chrome.storage.sync.set({ fab: $("fab").checked });
  say($("fab").checked
    ? "対応サイトに🎣ボタンを常時表示します（タブ再読込後に反映）"
    : "🎣ボタンを非表示にします（開いているタブは再読込後に反映）");
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
      say("自動ペアリング失敗: tamo serve を起動してから「再ペアリング」を押すか、`tamo token` の値を貼って保存してください");
    }
  }
});
