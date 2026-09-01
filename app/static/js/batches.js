(async function () {
  const listEl = document.getElementById('batchesList');
  const emptyEl = document.getElementById('batchesEmpty');

  async function refresh() {
    try {
      const data = await FB.api('/api/batches?limit=200');
      if (!data.batches.length) {
        listEl.innerHTML = '';
        emptyEl.style.display = 'block';
        return;
      }
      emptyEl.style.display = 'none';
      const div = FB.el('div', { class: 'list' });
      data.batches.forEach((b) => {
        const pct = b.total_jobs ? Math.round((b.completed_jobs / b.total_jobs) * 100) : 0;
        const item = FB.el('div', { class: 'batch-item' });
        const top = FB.el('div', { class: 'top' });
        const a = FB.el('a', { class: 'name', href: '/batches/' + b.id },
          '#' + b.id + ' · ' + b.name);
        top.appendChild(a);
        top.appendChild(FB.badge(b.status));
        item.appendChild(top);
        const prog = FB.el('div', { class: 'progress' });
        prog.appendChild(FB.el('div', { class: 'progress-bar', style: 'width:' + pct + '%' }));
        item.appendChild(prog);
        const meta = FB.el('div', { class: 'muted', style: 'font-size:12px' },
          b.media_type + ' · ' + b.completed_jobs + '/' + b.total_jobs +
          ' completed · ' + b.failed_jobs + ' failed · started ' + FB.fmtTime(b.created_at) +
          (b.elapsed_seconds ? ' · elapsed ' + FB.fmtElapsed(b.elapsed_seconds) : ''));
        item.appendChild(meta);
        const actions = FB.el('div', { class: 'job-actions' });
        actions.appendChild(btnAction('Pause', b.id, '/pause', b.status === 'running'));
        actions.appendChild(btnAction('Resume', b.id, '/resume', b.status === 'paused'));
        actions.appendChild(btnAction('Cancel', b.id, '/cancel', true, 'danger'));
        actions.appendChild(btnAction('Retry failed', b.id, '/retry-failed', true));
        actions.appendChild(btnAction('Duplicate', b.id, '/duplicate', true));
        actions.appendChild(linkAction('Manifest', '/api/batches/' + b.id + '/manifest'));
        actions.appendChild(linkAction('ZIP', '/api/batches/' + b.id + '/zip', true));
        item.appendChild(actions);
        div.appendChild(item);
      });
      listEl.innerHTML = '';
      listEl.appendChild(div);
    } catch (e) {
      listEl.innerHTML = '<span class="muted">' + e.message + '</span>';
    }
  }

  function linkAction(label, href, primary) {
    const a = FB.el('a', { class: 'btn btn-small' + (primary ? ' btn-primary' : ''), href: href, download: '' }, label);
    return a;
  }

  function btnAction(label, id, path, enabled, tone) {
    const b = FB.el('button', { class: 'btn btn-small' + (tone === 'danger' ? ' btn-danger' : '') }, label);
    b.disabled = !enabled;
    b.onclick = async () => {
      try {
        await FB.post('/api/batches/' + id + path);
        refresh();
      } catch (e) { alert(e.message); }
    };
    return b;
  }

  refresh();
  setInterval(refresh, 3000);
})();