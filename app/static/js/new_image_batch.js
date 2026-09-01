(async function () {
  const promptsEl = document.getElementById('prompts');
  const fileInput = document.getElementById('fileInput');
  const previewWrap = document.getElementById('previewWrap');
  const previewCount = document.getElementById('previewCount');
  const parseErrors = document.getElementById('parseErrors');
  let parsedJobs = [];
  let parseWarnings = [];

  function defaults() {
    return {
      provider: document.getElementById('provider').value,
      model: document.getElementById('model').value,
      size: document.getElementById('size').value,
      ratio: document.getElementById('ratio').value,
      output_format: document.getElementById('outputFormat').value,
      max_attempts: parseInt(document.getElementById('maxAttempts').value, 10) || 3,
    };
  }

  document.getElementById('btnUploadTxt').onclick = () => pick('.txt');
  document.getElementById('btnUploadCsv').onclick = () => pick('.csv,.txt');
  function pick(accept) {
    fileInput.accept = accept;
    fileInput.click();
  }
  fileInput.onchange = async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    const text = await file.text();
    promptsEl.value = text;
    await parse();
  };

  promptsEl.oninput = () => parse();

  async function parse() {
    const body = {
      media_type: 'image',
      text: promptsEl.value,
      defaults: defaults(),
    };
    try {
      const res = await FB.post('/api/batches/parse', body);
      parsedJobs = res.jobs;
      parseWarnings = res.errors || [];
      renderPreview();
    } catch (err) {
      parseErrors.style.display = 'block';
      parseErrors.textContent = 'Parse error: ' + err.message;
    }
  }

  function renderPreview() {
    parseErrors.style.display = parseWarnings.length ? 'block' : 'none';
    parseErrors.innerHTML = '';
    parseWarnings.slice(0, 8).forEach((w) => parseErrors.appendChild(FB.el('div', {}, '⚠ ' + w)));
    previewCount.textContent = parsedJobs.length + ' job(s)';
    previewWrap.innerHTML = '';
    if (!parsedJobs.length) return;
    const tbl = FB.el('table', {});
    const thead = FB.el('thead', {});
    const hr = FB.el('tr', {});
    ['#', 'Prompt', 'Type', 'Provider', 'Size', 'Ratio'].forEach((h) =>
      hr.appendChild(FB.el('th', {}, h)));
    thead.appendChild(hr);
    tbl.appendChild(thead);
    const tbody = FB.el('tbody', {});
    parsedJobs.slice(0, 50).forEach((j) => {
      const s = j.requested_settings || {};
      const tr = FB.el('tr', {});
      tr.appendChild(FB.el('td', {}, String(j.job_index + 1)));
      tr.appendChild(FB.el('td', {}, j.prompt));
      tr.appendChild(FB.el('td', {}, j.type));
      tr.appendChild(FB.el('td', {}, j.provider));
      tr.appendChild(FB.el('td', {}, s.size || '-'));
      tr.appendChild(FB.el('td', {}, s.ratio || '-'));
      tbody.appendChild(tr);
    });
    tbl.appendChild(tbody);
    previewWrap.appendChild(tbl);
  }

  document.getElementById('btnCreate').onclick = async () => {
    if (!parsedJobs.length) { await parse(); }
    if (!parsedJobs.length) { alert('No valid prompts.'); return; }
    const name = document.getElementById('batchName').value.trim() || 'Image batch';
    const maxAttempts = parseInt(document.getElementById('maxAttempts').value, 10) || 3;
    const body = {
      name: name,
      media_type: 'image',
      jobs: parsedJobs.map((j, i) => ({
        job_index: i,
        type: j.type,
        prompt: j.prompt,
        negative_prompt: j.negative_prompt || null,
        provider: j.provider,
        model: j.model,
        fallback_provider: document.getElementById('fallback').value || null,
        max_attempts: maxAttempts,
        requested_settings: j.requested_settings || {},
      })),
    };
    const btn = document.getElementById('btnCreate');
    btn.disabled = true;
    btn.textContent = 'Creating...';
    try {
      const created = await FB.post('/api/batches', body);
      location.href = '/batches/' + created.id;
    } catch (err) {
      btn.disabled = false;
      btn.textContent = 'Create Batch';
      const msg = String(err.message);
      if (/stale|pricing|Free pricing/i.test(msg)) {
        if (confirm(msg + '\n\nAcknowledge and create this large batch anyway?')) {
          body.pricing_acknowledged = true;
          try {
            const created2 = await FB.post('/api/batches', body);
            location.href = '/batches/' + created2.id;
          } catch (e2) { alert('Create failed: ' + e2.message); }
        }
      } else {
        alert('Create failed: ' + msg);
      }
    }
  };

  await parse();
})();