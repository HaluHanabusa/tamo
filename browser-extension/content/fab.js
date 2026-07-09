// tamo scoop — 常駐ボタン(FAB)
// 対応サイトの画面右上に🎣ボタンを固定表示し、ワンクリックで「掬う→投函→結果トースト」。
// popupを開く手間をなくす。設定(popupのチェックボックス)でOFFにできる。
(() => {
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
      return toast(host, "拡張が更新されました — このタブを再読み込み(F5)すると直ります", false);
    }
    btn.disabled = true;
    btn.textContent = "…";
    // 自動スクロール中は n/25 の進捗を出す（長い会話で十数秒黙るとフリーズに見える）
    window.__tamo.onScrollStep = (i, max) => {
      btn.textContent = String(i);
      btn.title = `過去分を読み込み中 ${i}/${max}`;
    };
    try {
      const res = await window.__tamoRun();
      if (!res.ok) return toast(host, `抽出失敗: ${res.error}`, false);
      const p = res.payload;
      const nAtt = p.messages.reduce((n, m) => n + (m.attachments || []).length, 0);
      const post = await chrome.runtime.sendMessage({ type: "tamo.post", payload: p });
      if (post.ok) {
        const trunc = p.note && p.note.includes("[上限切詰め]") ? " ⚠一部切詰め" : "";
        toast(host, `tamoに投函 ✓ ${res.adapter}${res.warn ? "(fallback)" : ""} msg=${p.messages.length} att=${nAtt}${trunc}`, true);
      } else {
        toast(host, `投函失敗: ${post.error || "HTTP " + post.status}`, false);
      }
    } catch (e) {
      toast(host, `エラー: ${e.message}`, false);
    } finally {
      window.__tamo.onScrollStep = null;
      btn.disabled = false;
      btn.textContent = "🎣";
      btn.title = "この会話をtamoに掬う";
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
      <div class="wrap"><button title="この会話をtamoに掬う">🎣</button></div>`;
    const wrap = shadow.querySelector(".wrap");
    const btn = shadow.querySelector("button");
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
