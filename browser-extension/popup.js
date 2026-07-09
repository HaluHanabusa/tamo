// tamo scoop — popup
const $ = (id) => document.getElementById(id);
const say = (t) => { $("status").textContent = t; };
// i18n: _locales/{en,ja}/messages.json。取れない環境ではHTMLの英語既定文を残す
const t = (key, subs) => {
  try {
    return chrome.i18n.getMessage(key, subs) || key;
  } catch (_e) {
    return key;
  }
};

// 静的ラベルの差し替え（HTML側は英語のフォールバック文）
for (const el of document.querySelectorAll("[data-i18n]")) {
  const m = t(el.dataset.i18n);
  if (m && m !== el.dataset.i18n) el.textContent = m;
}
for (const el of document.querySelectorAll("[data-i18n-placeholder]")) {
  const m = t(el.dataset.i18nPlaceholder);
  if (m && m !== el.dataset.i18nPlaceholder) el.placeholder = m;
}

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
  say(t("saved"));
};

$("pair").onclick = async () => {
  // トークン不一致(403)からの復帰導線。/pair はlocalhost限定・サーバの現行トークンを返す
  say(t("pairing"));
  const r = await chrome.runtime.sendMessage({ type: "tamo.pair" });
  if (r && r.ok) {
    const s = await chrome.storage.sync.get({ token: "" });
    $("token").value = s.token;
    say(t("pairDone"));
  } else {
    say(t("pairFail"));
  }
};

$("health").onclick = async () => {
  say(t("healthChecking"));
  const r = await chrome.runtime.sendMessage({ type: "tamo.health" });
  if (r.ok) {
    say($("token").value.trim() ? t("healthOkToken") : t("healthOkNoToken"));
    return;
  }
  if (r.tokenBad) return say(t("healthTokenBad"));
  say(t("healthNg", [String(r.error || r.status)]));
};

$("recall").onclick = async () => {
  const q = $("rq").value.trim();
  if (!q) return say(t("recallNeedQuery"));
  say(t("recallFetching"));
  const r = await chrome.runtime.sendMessage({ type: "tamo.recall", query: q, source: $("rsrc").value });
  if (!r.ok) return say(t("recallFail", [String(r.error)]));
  if (r.text.includes("(該当なし")) {  // 空振りを「コピー成功」と偽らない
    return say(t("recallEmpty", [q]));
  }
  await navigator.clipboard.writeText(r.text);
  say(t("recallCopied", [String(r.text.length)]));
};

$("rq").onkeydown = (e) => { if (e.key === "Enter") $("recall").click(); };

$("scoop").onclick = async () => {
  say(t("scooping"));
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
    return say(t("scoopRestricted", [e.message]));
  }
  if (!res || !res.ok) return say(t("scoopFail", [(res && res.error) || t("unknownError")]));
  const p = res.payload;
  const nAtt = p.messages.reduce((n, m) => n + (m.attachments || []).length, 0);
  say(`${t("extractOk", [res.adapter])}${res.warn ? " ⚠" + res.warn : ""}\n` +
      `messages=${p.messages.length} attachments=${nAtt}\n${t("posting")}`);
  const post = await chrome.runtime.sendMessage({ type: "tamo.post", payload: p });
  if (post.ok) {
    say(`${t("postDone", [p.session])}\nmessages=${p.messages.length} attachments=${nAtt}` +
        (p.note ? `\n⚠ ${p.note}` : "") + `\n${t("postIngestNote")}`);
  } else {
    say(t("postFail", [String(post.error || "HTTP " + post.status)]) +
        (post.status === 403 ? `\n${t("hint403")}` : ""));
  }
};

$("fab").onchange = async () => {
  await chrome.storage.sync.set({ fab: $("fab").checked });
  say($("fab").checked ? t("fabOn") : t("fabOff"));
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
      say(t("autoPaired"));
    } else {
      say(t("autoPairFail"));
    }
  }
});
