// tamo scoop — content dispatcher
// popup からの "tamo.scoop" を受け、ホスト名に合うアダプタ → 失敗したら generic の順で実行。
(() => {
  async function run() {
    const sites = window.__tamoSites || {};
    window.__tamo.resetBudget();
    const fn = sites[location.hostname];
    if (fn) {
      try {
        const payload = await fn();
        return { ok: true, payload, adapter: location.hostname };
      } catch (e) {
        // API形式ドリフト等 → genericへフォールバック（理由は残す。既存noteは潰さず追記）
        try {
          const payload = await sites.__generic();
          payload.note = (payload.note ? payload.note + " / " : "") + `site adapter failed: ${e.message}`;
          return { ok: true, payload, adapter: "generic(fallback)", warn: e.message };
        } catch (e2) {
          return { ok: false, error: `${e.message} / generic: ${e2.message}` };
        }
      }
    }
    try {
      const payload = await sites.__generic();
      return { ok: true, payload, adapter: "generic" };
    } catch (e) {
      return { ok: false, error: e.message };
    }
  }

  window.__tamoRun = run;  // 常駐ボタン(fab.js)から直接呼べるように公開

  chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    if (msg && msg.type === "tamo.scoop") {
      run().then(sendResponse);
      return true; // async
    }
    return false;
  });
})();
