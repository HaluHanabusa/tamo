// tamo scoop — 常駐ボタン(FAB)
// 対応サイトの画面右上に🎣ボタンを固定表示し、ワンクリックで「掬う→投函→結果トースト」。
// popupを開く手間をなくす。設定(popupのチェックボックス)でOFFにできる。
(() => {
  // i18n（孤児化してchrome.i18nが失われた場合は英語のフォールバック文を使う）
  const t = (key, subs, fallback) => {
    try {
      return chrome.i18n.getMessage(key, subs) || fallback || key;
    } catch (_e) {
      return fallback || key;
    }
  };

  async function enabled() {
    if (!window.__tamo || !window.__tamo.alive()) return false;
    try {
      const d = await chrome.storage.sync.get({ fab: true });
      return d.fab;
    } catch (_e) {
      return false;
    }
  }

  function toast(host, text, ok) {
    let t = host.querySelector(".tamo-toast");
    if (!t) {
      t = document.createElement("div");
      t.className = "tamo-toast";
      host.appendChild(t);
    }
    t.textContent = text;
    t.style.background = ok ? "#0a7d33" : "#b3261e";
    t.style.opacity = "1";
    clearTimeout(t._h);
    t._h = setTimeout(() => { t.style.opacity = "0"; }, 5000);
  }

  async function scoopAndPost(host, btn) {
    if (!window.__tamo.alive()) {
      // 拡張更新でこのタブのスクリプトが孤児化している
      return toast(host, t("fabOrphaned", null,
        "The extension was updated — reload this tab (F5) to fix it"), false);
    }
    btn.disabled = true;
    btn.textContent = "…";
    // 自動スクロール中は n/25 の進捗を出す（長い会話で十数秒黙るとフリーズに見える）
    window.__tamo.onScrollStep = (i, max) => {
      btn.textContent = String(i);
      btn.title = t("fabLoading", [String(i), String(max)], `Loading history ${i}/${max}`);
    };
    try {
      const res = await window.__tamoRun();
      if (!res.ok) return toast(host, t("scoopFail", [String(res.error)], `Extraction failed: ${res.error}`), false);
      const p = res.payload;
      const nAtt = p.messages.reduce((n, m) => n + (m.attachments || []).length, 0);
      const post = await chrome.runtime.sendMessage({ type: "tamo.post", payload: p });
      if (post.ok) {
        const trunc = p.note && p.note.includes("[上限切詰め]") ? ` ${t("fabTruncated", null, "⚠ partially truncated")}` : "";
        toast(host, `${t("fabDelivered", null, "Delivered to tamo ✓")} ${res.adapter}${res.warn ? "(fallback)" : ""} msg=${p.messages.length} att=${nAtt}${trunc}`, true);
      } else {
        const detail = post.error || "HTTP " + post.status;
        toast(host, t("postFail", [String(detail)], `Delivery failed: ${detail}`), false);
      }
    } catch (e) {
      toast(host, t("fabError", [e.message], `Error: ${e.message}`), false);
    } finally {
      window.__tamo.onScrollStep = null;
      btn.disabled = false;
      btn.textContent = "🎣";
      btn.title = t("fabTitle", null, "Scoop this conversation into tamo");
    }
  }

  async function mount() {
    if (!(await enabled()) || document.getElementById("tamo-fab-root")) return;
    const root = document.createElement("div");
    root.id = "tamo-fab-root";
    const shadow = root.attachShadow({ mode: "closed" });  // サイトのCSSと相互不干渉
    shadow.innerHTML = `
      <style>
        .wrap { position: fixed; top: 72px; right: 14px; z-index: 2147483646;
                display: flex; flex-direction: column; align-items: flex-end; gap: 6px;
                font: 12px/1.4 system-ui, sans-serif; }
        button { width: 40px; height: 40px; border-radius: 50%; border: none; cursor: pointer;
                 background: #1a1a1a; color: #fff; font-size: 18px;
                 box-shadow: 0 2px 8px rgba(0,0,0,.35); opacity: .55; transition: opacity .15s; }
        button:hover { opacity: 1; }
        button:disabled { cursor: wait; }
        .tamo-toast { max-width: 300px; padding: 6px 10px; border-radius: 6px; color: #fff;
                      opacity: 0; transition: opacity .3s; word-break: break-all; }
      </style>
      <div class="wrap"><button>🎣</button></div>`;
    const wrap = shadow.querySelector(".wrap");
    const btn = shadow.querySelector("button");
    btn.title = t("fabTitle", null, "Scoop this conversation into tamo");
    btn.onclick = () => scoopAndPost(wrap, btn);
    document.documentElement.appendChild(root);
  }

  // SPA遷移でも生存するよう、初期化+定期の存在チェック（軽量）
  mount();
  const iv = setInterval(() => {
    if (!window.__tamo || !window.__tamo.alive()) {
      clearInterval(iv);  // 孤児化したら静かに停止（エラーを撒き散らさない）
      return;
    }
    mount();
  }, 4000);
})();
