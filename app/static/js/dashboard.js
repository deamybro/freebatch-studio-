(async function () {
  const grid = document.getElementById('statGrid');
  const queueEl = document.getElementById('queueSnapshot');
  const provEl = document.getElementById('providerStatus');
  const recentEl = document.getElementById('recentBatches');
  const lastEl = document.getElementById('lastRefresh');

  async function refresh() {
    try {
      const d = await FB.api('/api/dashboard');
      lastEl.textContent = 'updated ' + new Date().toLocaleTimeString();
      renderStats(d);
      renderQueue(d);
      renderProviders(d);
      renderBatches(d);
    } catch (e) {
      queueEl.innerHTML = '<span class="muted">' + e.message + '</span>';
    }
  }

  function renderStats(d) {
    const j = d.jobs || {};
    grid.innerHTML = '';
    const items = [
      ['Batches today', d.batches_today, ''],
      ['Queued', (j.queued_remote || 0) + (j.pending || 0), ''],
      ['Running', (j.processing || 0) + (j.polling || 0), ''],
      ['Completed', j.completed || 0, 'ok'],
      ['Failed', j.failed || 0, j.failed ? 'bad' : 'ok'],
      ['Storage', (d.storage_used_mb ? d.storage_used_mb + ' MB' : '0 MB'), ''],
    ];
    items.forEach(([label, value, tone]) => {
      const el = FB.el('div', { class: 'stat' });
      el.appendChild(FB.el('span', { class: tone ? 'badge badge-' + tone : '' }, String(value)));
      el.appendChild(FB.el('label', {}, label));
      grid.appendChild(el);
    });
  }

  function renderQueue(d) {
    const j = d.jobs || {};
    queueEl.innerHTML = '';
    const rows = [
      ['pending', j.pending || 0], ['queued_remote', j.queued_remote || 0],
      ['processing', j.processing || 0], ['polling', j.polling || 0],
      ['completed', j.completed || 0], ['failed', j.failed || 0],
      ['cancelled', j.cancelled || 0],
    ];
    rows.forEach(([k, v]) => {
      queueEl.appendChild(FB.el('div', { class: 'k' }, k));
      queueEl.appendChild(FB.el('div', { class: 'v' }, String(v)));
    });
  }

  function renderProviders(d) {
    provEl.innerHTML = '';
    const p = d.providers || {};
    const names = ['agnes_image', 'agnes_video', 'ai_horde', 'wangp', 'comfyui'];
    names.forEach((n) => {
      const h = p[n];
      let txt = n;
      if (h) {
        if (h.state === 'unavailable' || h.reachable === false) txt += ' · offline';
        else if (h.reachable) txt += ' · online';
        else txt += ' · not configured';
      } else {
        txt += ' · —';
      }
      provEl.appendChild(FB.el('div', { class: 'k' }, n));
      provEl.appendChild(FB.el('div', { class: 'v' }, txt));
    });
    provEl.appendChild(FB.el('div', { class: 'k' }, 'FREE-ONLY'));
    provEl.appendChild(FB.el('div', { class: 'v' }, d.free_only_mode ? 'ON' : 'OFF'));
  }

  function renderBatches(d) {
    const div = document.createElement('div');
    div.className = 'list';
    FB.api('/api/batches?limit=5').then((data) => {
      if (!data.batches.length) {
        recentEl.innerHTML = '<span class="muted">No batches yet. Create one from the left menu.</span>';
        return;
      }
      div.innerHTML = '';
      data.batches.forEach((b) => {
        const pct = b.total_jobs ? Math.round((b.completed_jobs / b.total_jobs) * 100) : 0;
        const item = FB.el('div', { class: 'batch-item' });
        const top = FB.el('div', { class: 'top' });
        const a = FB.el('a', { class: 'name', href: '/batches/' + b.id }, '#' + b.id + ' ' + b.name);
        top.appendChild(a);
        top.appendChild(FB.badge(b.status));
        item.appendChild(top);
        const prog = FB.el('div', { class: 'progress' });
        prog.appendChild(FB.el('div', { class: 'progress-bar', style: 'width:' + pct + '%' }));
        item.appendChild(prog);
        const meta = FB.el('div', { class: 'muted', style: 'font-size:12px' },
          b.completed_jobs + '/' + b.total_jobs + ' · failed ' + b.failed_jobs +
          ' · ' + b.media_type);
        item.appendChild(meta);
        div.appendChild(item);
      });
      recentEl.innerHTML = '';
      recentEl.appendChild(div);
    }).catch(() => {});
  }

  refresh();
  setInterval(refresh, 4000);
})();