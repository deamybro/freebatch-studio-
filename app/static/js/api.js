/* FreeBatch Studio - lightweight API + DOM helpers (no framework) */
(function () {
  const csrf = document.querySelector('meta[name="csrf-token"]');
  const TOKEN = csrf ? csrf.content : '';

  window.FB = {
    TOKEN: TOKEN,
    async api(path, opts) {
      opts = opts || {};
      opts.headers = Object.assign(
        { 'Content-Type': 'application/json' },
        opts.headers || {}
      );
      if (opts.method && opts.method.toUpperCase() !== 'GET') {
        opts.headers['X-CSRF-Token'] = TOKEN;
      }
      const resp = await fetch(path, opts);
      let data = null;
      const text = await resp.text();
      try { data = text ? JSON.parse(text) : null; } catch (e) { data = text; }
      if (!resp.ok) {
        const detail = data && data.detail ? data.detail : (text || resp.status);
        throw new Error(detail);
      }
      return data;
    },
    post(path, body) {
      return this.api(path, {
        method: 'POST',
        body: JSON.stringify(body || {}),
      });
    },
    put(path, body) {
      return this.api(path, {
        method: 'PUT',
        body: JSON.stringify(body || {}),
      });
    },
    el(tag, attrs, text) {
      const el = document.createElement(tag);
      if (attrs) for (const k in attrs) {
        if (k === 'class') el.className = attrs[k];
        else if (k === 'html') el.innerHTML = attrs[k];
        else el.setAttribute(k, attrs[k]);
      }
      if (text !== undefined) el.textContent = text;
      return el;
    },
    fmtElapsed(sec) {
      if (sec === null || sec === undefined) return '';
      sec = Math.max(0, Math.round(sec));
      const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
      if (h) return h + 'h ' + m + 'm ' + s + 's';
      if (m) return m + 'm ' + s + 's';
      return s + 's';
    },
    fmtTime(iso) {
      if (!iso) return '';
      const d = new Date(iso);
      return isNaN(d) ? iso : d.toLocaleString();
    },
    badge(state) {
      const map = {
        running: 'ok', paused: 'warn', completed: 'ok', cancelled: 'bad',
        pending: 'muted', queued_remote: 'warn', processing: 'warn',
        polling: 'warn', failed: 'bad', completed_job: 'ok',
      };
      const cls = map[state] || 'muted';
      const el = this.el('span', { class: 'badge badge-' + cls }, state);
      return el;
    },
    pill(status) {
      return this.el('span', { class: 'job-pill pill-' + status }, status);
    },
  };
})();