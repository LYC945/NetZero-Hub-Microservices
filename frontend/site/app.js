// NetZero Hub front end: shared helpers. All calls go to the API gateway on the same origin
// (/wma001/, /ea01/, /wma002/), so the browser needs no CORS and never sees the service ports.

const Hub = {
  // One case id per signed-in session, sent as X-Case-Id to every service, so a household's
  // journey across WMA001, EA01 and WMA002 can be followed in all three event logs (Part F).
  caseId() {
    let id = null;
    try { id = sessionStorage.getItem("nzh_case"); } catch (e) {}
    if (!id) {
      id = "case-web-" + Math.random().toString(16).slice(2, 10);
      try { sessionStorage.setItem("nzh_case", id); } catch (e) {}
    }
    return id;
  },

  user() {
    try { return sessionStorage.getItem("nzh_user") || "guest@netzerohub.local"; } catch (e) { return "guest@netzerohub.local"; }
  },

  async api(method, path, body) {
    const t0 = performance.now();
    const opts = { method, headers: { "X-Case-Id": Hub.caseId() } };
    if (body !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    let res, data = null;
    try {
      res = await fetch(path, opts);
    } catch (e) {
      return { ok: false, status: 0, data: null, meta: { error: "Gateway not reachable" } };
    }
    try { data = await res.json(); } catch (e) {}
    return {
      ok: res.ok, status: res.status, data,
      meta: {
        service: res.headers.get("X-Service") || "gateway",
        ms: res.headers.get("X-Response-Time-ms"),
        rtt: Math.round(performance.now() - t0),
        caseId: res.headers.get("X-Case-Id") || Hub.caseId(),
      },
    };
  },

  esc(s) {
    return String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  },

  // small footer under every result: which microservice answered, how fast, which case
  meta(r) {
    if (r.status === 0) return `<div class="meta bad">Gateway not reachable</div>`;
    if (r.status === 502 || r.status === 504) return `<div class="meta bad">HTTP ${r.status} – the service is down; the other services keep working</div>`;
    return `<div class="meta">answered by <b>${Hub.esc(r.meta.service)}</b> · HTTP ${r.status} · ` +
      `${r.meta.ms ? r.meta.ms + " ms in service, " : ""}${r.meta.rtt} ms round trip · ${Hub.esc(r.meta.caseId)}</div>`;
  },

  setBusy(btn, busy, label) {
    btn.disabled = busy;
    btn.textContent = busy ? "Working…" : label;
  },
};
