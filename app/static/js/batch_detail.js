(async function () {
  const BATCH_ID = window.BATCH_ID;
  let offset = 0;
  const LIMIT = 30;
  let statusFilter = '';
  let jobsTotal = 0;

  const jobsEl = document.getElementById('jobsList');
  const pageInfo = document.getElementById('pageInfo');

  function setStat(id, value) {
    document.getElementById(id).textContent = String(value);
  }

  async function refresh() {
    try {
      const url = '/api/batches/' + BATCH_ID + '/jobs?offset=' + offset +
        '&limit=' + LIMIT + (statusFilter ? '&status=' + statusFilter : '');
      const d = await FB.api(url);
      jobsTotal = d.total;
      const b = d.batch;
      setStat('stTotal', b.total_jobs);
      setStat('stDone', b.completed_jobs);
      setStat('stFailed', b.failed_jobs);
      setStat('stPending', (d.counts.pending || 0));
      setStat('stActive', (d.counts.processing || 0) + (d.counts.polling || 0) + (d.counts.queued_remote || 0));
      const pct = b.total_jobs ? Math.round((b.completed_jobs / b.total_jobs) * 100) : 0;
      document.getElementById('progressBar').style.width = pct + '%';
      const meta = document.getElementById('batchMeta');
      meta.textContent = 'status: ' + b.status + ' · ' + b.media_type +
        ' · created ' + FB.fmtTime(b.created_at) +
        (b.elapsed_seconds ? ' · elapsed ' + FB.fmtElapsed(b.elapsed_seconds) : '');
      renderJobs(d.jobs);
      pageInfo.textContent = (offset + 1) + '–' + Math.min(offset + LIMIT, jobsTotal) + ' of ' + jobsTotal;
      document.getElementById('btnPrev').disabled = offset <= 0;
      document.getElementById('btnNext').disabled = offset + LIMIT >= jobsTotal;
    } catch (e) {
      jobsEl.innerHTML = '<span class="muted">' + e.message + '</span>';
    }
  }

  function renderJobs(jobs) {
    jobsEl.innerHTML = '';
    if (!jobs.length) {
      jobsEl.appendChild(FB.el('div', { class: 'muted' }, 'No jobs match this filter.'));
      return;
    }
    jobs.forEach((j) => {
      const card = FB.el('div', { class: 'job-card' });
      const thumb = thumbnail(j);
      if (thumb) card.appendChild(thumb);
      const main = FB.el('div', { class: 'job-main' });
      const head = FB.el('div', { style: 'display:flex;justify-content:space-between;gap:8px;flex-wrap:wrap;align-items:center' });
      const prompt = FB.el('div', { class: 'job-prompt' }, '#' + (j.job_index + 1) + ' · ' + j.prompt);
      head.appendChild(prompt);
      head.appendChild(FB.pill(j.status));
      main.appendChild(head);
      const meta = FB.el('div', { class: 'job-meta' },
        (j.provider_name || j.provider) + ' · ' + (j.model || 'auto') +
        (j.fallback_provider ? ' · fallback: ' + j.fallback_provider : '') +
        ' · attempts ' + j.attempts + '/' + j.max_attempts +
        (j.generation_seconds != null ? ' · ' + j.generation_seconds + 's' : ''));
      main.appendChild(meta);
      const req = j.requested_settings || {};
      const act = j.actual_settings || {};
      if (Object.keys(req).length || Object.keys(act).length) {
        const det = FB.el('div', { class: 'job-meta' },
          'requested: ' + JSON.stringify(req) + (Object.keys(act).length ? ' · actual: ' + JSON.stringify(act) : ''));
        main.appendChild(det);
      }
      if (j.error_message) {
        const err = FB.el('div', { class: 'alert alert-danger', style: 'margin:6px 0 0' },
          (j.error_type ? j.error_type + ': ' : '') + j.error_message);
        main.appendChild(err);
      }
      const actions = FB.el('div', { class: 'job-actions', style: 'margin-top:8px' });
      if (j.status === 'failed' || j.status === 'cancelled') {
        const retry = FB.el('button', { class: 'btn btn-small' }, 'Retry');
        retry.onclick = async () => {
          try { await FB.post('/api/jobs/' + j.id + '/retry'); refresh(); }
          catch (e) { alert(e.message); }
        };
        actions.appendChild(retry);
      }
      if (j.local_output_path) {
        const open = FB.el('a', { class: 'btn btn-small btn-primary', href: '/api/jobs/' + j.id + '/file', target: '_blank' }, 'Open file');
        actions.appendChild(open);
      }
      if (j.remote_video_id || j.remote_task_id) {
        const info = FB.el('button', { class: 'btn btn-small' }, 'Remote info');
        info.onclick = async () => {
          try {
            const ev = await FB.api('/api/jobs/' + j.id + '/events');
            const list = ev.map((x) => x.timestamp + ' ' + x.event_type + ' ' + (x.metadata || '')).join('\n');
            alert('remote_video_id: ' + (j.remote_video_id || '-') + '\nremote_task_id: ' + (j.remote_task_id || '-') + '\n\n' + (list || 'no events'));
          } catch (e) { alert(e.message); }
        };
        actions.appendChild(info);
      }
      main.appendChild(actions);
      card.appendChild(main);
      jobsEl.appendChild(card);
    });
  }

  function thumbnail(j) {
    if (!j.local_output_path) return null;
    const src = '/api/jobs/' + j.id + '/file';
    const isVideo = /\.mp4$/i.test(j.local_output_path);
    if (isVideo) {
      return FB.el('video', { class: 'job-thumb', controls: '', preload: 'metadata', src: src });
    }
    return FB.el('img', { class: 'job-thumb', src: src, loading: 'lazy' });
  }

  document.getElementById('btnPrev').onclick = () => { offset = Math.max(0, offset - LIMIT); refresh(); };
  document.getElementById('btnNext').onclick = () => { offset += LIMIT; refresh(); };

  document.querySelectorAll('.chip').forEach((chip) => {
    chip.onclick = () => {
      document.querySelectorAll('.chip').forEach((c) => c.classList.remove('active'));
      chip.classList.add('active');
      statusFilter = chip.dataset.status || '';
      offset = 0;
      refresh();
    };
  });

  ['btnPause', 'btnResume', 'btnCancel', 'btnRetryFailed', 'btnDuplicate'].forEach((id) => {
    const action = id.replace('btn', '').toLowerCase();
    document.getElementById(id).onclick = async () => {
      try {
        const res = await FB.post('/api/batches/' + BATCH_ID + '/' + action);
        if (action === 'duplicate' && res.new_batch_id) {
          location.href = '/batches/' + res.new_batch_id;
          return;
        }
        refresh();
      } catch (e) { alert(e.message); }
    };
  });

  refresh();
  setInterval(refresh, 3000);
})();