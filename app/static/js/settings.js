(async function () {
  const form = document.getElementById('settingsForm');
  const secretEl = document.getElementById('secretStatus');
  let current = {};

  const FIELDS = [
    { key: 'output_dir', label: 'Output directory', type: 'text' },
    { key: 'poll_interval_seconds', label: 'Poll interval (seconds)', type: 'number' },
    { key: 'max_attempts', label: 'Default max retries', type: 'number' },
    { key: 'request_timeout_seconds', label: 'Request timeout (s)', type: 'number' },
    { key: 'download_timeout_seconds', label: 'Download timeout (s)', type: 'number' },
    { key: 'max_download_size_mb', label: 'Max download size (MB)', type: 'number' },
    { key: 'max_reference_size_mb', label: 'Max reference size (MB)', type: 'number' },
    { key: 'pricing_stale_days', label: 'Pricing stale threshold (days)', type: 'number' },
    { key: 'large_batch_warn_threshold', label: 'Large-batch warn threshold (jobs)', type: 'number' },
    { key: 'free_only_mode', label: 'FREE-ONLY mode', type: 'checkbox' },
    { key: 'horde_anonymous', label: 'AI Horde anonymous mode', type: 'checkbox' },
    { key: 'horde_steps', label: 'AI Horde steps', type: 'number' },
    { key: 'zip_include_failed', label: 'Include failed outputs in ZIP', type: 'checkbox' },
    { key: 'zip_include_metadata', label: 'Include manifests in ZIP', type: 'checkbox' },
    { key: 'log_level', label: 'Log level', type: 'select',
      options: ['DEBUG', 'INFO', 'WARNING', 'ERROR'] },
    { key: 'rate_limits', label: 'Rate limits (JSON: "provider:type" -> RPM)', type: 'textarea' },
  ];

  async function load() {
    current = await FB.api('/api/settings');
    render();
    renderSecrets();
  }

  function render() {
    form.innerHTML = '';
    FIELDS.forEach((f) => {
      const card = FB.el('div', { class: 'card', style: 'margin:0' });
      card.appendChild(FB.el('h2', {}, f.label));
      let input;
      const raw = current[f.key];
      if (f.type === 'checkbox') {
        input = FB.el('input', { type: 'checkbox' });
        input.checked = raw === true || raw === 'true' || raw === '1';
      } else if (f.type === 'select') {
        input = FB.el('select', {});
        f.options.forEach((o) => {
          const opt = FB.el('option', { value: o }, o);
          if (String(raw) === o) opt.selected = true;
          input.appendChild(opt);
        });
      } else if (f.type === 'textarea') {
        input = FB.el('textarea', { rows: '6' }, typeof raw === 'object' ? JSON.stringify(raw, null, 2) : raw);
      } else {
        input = FB.el('input', { type: f.type, value: raw === undefined || raw === null ? '' : String(raw) });
      }
      input.dataset.key = f.key;
      input.dataset.ftype = f.type;
      card.appendChild(input);
      form.appendChild(card);
    });
  }

  function renderSecrets() {
    secretEl.innerHTML = '';
    for (const k in current.secret_status) {
      secretEl.appendChild(FB.el('div', { class: 'k' }, k));
      secretEl.appendChild(FB.el('div', { class: 'v' }, current.secret_status[k]));
    }
  }

  document.getElementById('btnSave').onclick = async () => {
    const values = {};
    form.querySelectorAll('[data-key]').forEach((el) => {
      const key = el.dataset.key;
      const ftype = el.dataset.ftype;
      if (ftype === 'checkbox') values[key] = el.checked;
      else if (ftype === 'textarea') {
        const text = el.value.trim();
        try { values[key] = JSON.parse(text); }
        catch (e) { alert('Invalid JSON for ' + key); return; }
      }
      else if (key === 'rate_limits') values[key] = el.value.trim();
      else values[key] = el.value;
    });
    const btn = document.getElementById('btnSave');
    btn.disabled = true;
    btn.textContent = 'Saving...';
    try {
      await FB.put('/api/settings', { values });
      btn.disabled = false;
      btn.textContent = 'Saved ✓';
      setTimeout(() => { btn.textContent = 'Save'; }, 1500);
      load();
    } catch (e) {
      btn.disabled = false;
      btn.textContent = 'Save';
      alert('Save failed: ' + e.message);
    }
  };

  load();
})();