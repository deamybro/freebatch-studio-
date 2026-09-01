(async function () {
  const promptsEl = document.getElementById('prompts');
  const fileInput = document.getElementById('fileInput');
  const previewWrap = document.getElementById('previewWrap');
  const previewCount = document.getElementById('previewCount');
  const parseErrors = document.getElementById('parseErrors');
  const jobTypeEl = document.getElementById('jobType');
  const imageUrlField = document.getElementById('imageUrlField');
  const keyframeField = document.getElementById('keyframeField');
  let parsedJobs = [];
  let parseWarnings = [];

  function defaults() {
    return {
      provider: 'agnes_video',
      model: 'agnes-video-v2.0',
      kind: jobTypeEl.value,
      duration: document.getElementById('duration').value,
      fps: parseInt(document.getElementById('fps').value, 10) || 24,
      ratio: document.getElementById('ratio').value,
      tier: document.getElementById('tier').value,
      seed: document.getElementById('seed').value ? parseInt(document.getElementById('seed').value, 10) : null,
      negative_prompt: document.getElementById('negativePrompt').value || null,
      image_url: document.getElementById('imageUrl').value.trim() || null,
      keyframe_urls: document.getElementById('keyframes').value.split('\n').map(s => s.trim()).filter(Boolean),
    };
  }

  jobTypeEl.onchange = () => {
    imageUrlField.style.display = jobTypeEl.value === 'image_to_video' ? 'block' : 'none';
    keyframeField.style.display = jobTypeEl.value === 'keyframes' ? 'block' : 'none';
    parse();
  };
  imageUrlField.style.display = jobTypeEl.value === 'image_to_video' ? 'block' : 'none';
  keyframeField.style.display = jobTypeEl.value === 'keyframes' ? 'block' : 'none';

  // --- image upload helpers ---
  const dropZone = document.getElementById('dropZone');
  const imageFile = document.getElementById('imageFile');
  const previewImg = document.getElementById('previewImg');
  const imagePreview = document.getElementById('imagePreview');
  const uploadStatus = document.getElementById('uploadStatus');
  const btnRemove = document.getElementById('btnRemoveImage');
  const imageUrlEl = document.getElementById('imageUrl');
  const kfDropZone = document.getElementById('kfDropZone');
  const kfFiles = document.getElementById('kfFiles');
  const kfPreview = document.getElementById('kfPreview');
  const kfStatus = document.getElementById('kfStatus');
  const kfTextarea = document.getElementById('keyframes');

  async function uploadImageFile(file) {
    const fd = new FormData();
    fd.append('file', file);
    const res = await fetch('/api/upload-image', {
      method: 'POST',
      headers: { 'X-CSRF-Token': FB.TOKEN },
      body: fd,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || res.statusText || 'upload failed');
    if (!data.url) throw new Error('no URL returned');
    return data.url;
  }

  function showPreview(file) {
    const url = URL.createObjectURL(file);
    previewImg.src = url;
    imagePreview.style.display = 'block';
    btnRemove.style.display = 'inline-block';
  }

  // image-to-video single file
  if (dropZone && imageFile) {
    dropZone.addEventListener('click', () => imageFile.click());
    dropZone.addEventListener('dragover', (e) => { e.preventDefault(); dropZone.classList.add('dragover'); });
    dropZone.addEventListener('dragleave', () => dropZone.classList.remove('dragover'));
    dropZone.addEventListener('drop', async (e) => {
      e.preventDefault(); dropZone.classList.remove('dragover');
      const f = e.dataTransfer.files[0]; if (f) handleImageFile(f);
    });
    imageFile.addEventListener('change', (e) => { const f = e.target.files[0]; if (f) handleImageFile(f); });
    btnRemove.addEventListener('click', () => {
      imageFile.value = ''; imageUrlEl.value = ''; uploadStatus.textContent = '';
      imagePreview.style.display = 'none'; previewImg.src = ''; parse();
    });
    imageUrlEl.addEventListener('input', () => {
      // if user types a URL, show it as preview if it's an image URL
      const v = imageUrlEl.value.trim();
      if (v && v.startsWith('http')) {
        previewImg.src = v; imagePreview.style.display = 'block';
      }
      parse();
    });
  }

  async function handleImageFile(file) {
    if (!file.type.startsWith('image/')) { uploadStatus.textContent = 'only images allowed'; return; }
    if (file.size > 10 * 1024 * 1024) { uploadStatus.textContent = 'file too large (max 10MB)'; return; }
    showPreview(file);
    uploadStatus.textContent = 'Uploading...';
    try {
      const url = await uploadImageFile(file);
      imageUrlEl.value = url;
      uploadStatus.textContent = 'Uploaded ✓ ' + url;
      document.getElementById('urlStatus').textContent = 'valid public URL ✓';
      parse();
    } catch (err) {
      uploadStatus.textContent = 'Upload failed: ' + err.message;
    }
  }

  // keyframes multi
  if (kfDropZone && kfFiles) {
    kfDropZone.addEventListener('click', () => kfFiles.click());
    kfDropZone.addEventListener('dragover', (e) => { e.preventDefault(); kfDropZone.classList.add('dragover'); });
    kfDropZone.addEventListener('dragleave', () => kfDropZone.classList.remove('dragover'));
    kfDropZone.addEventListener('drop', async (e) => {
      e.preventDefault(); kfDropZone.classList.remove('dragover');
      const files = Array.from(e.dataTransfer.files).filter(f => f.type.startsWith('image/'));
      if (files.length) handleKfFiles(files);
    });
    kfFiles.addEventListener('change', (e) => {
      const files = Array.from(e.target.files).filter(f => f.type.startsWith('image/'));
      if (files.length) handleKfFiles(files);
    });
  }

  async function handleKfFiles(files) {
    kfPreview.style.display = 'grid'; kfPreview.innerHTML = ''; kfStatus.textContent = 'Uploading ' + files.length + ' image(s)...';
    const urls = [];
    for (const file of files) {
      if (file.size > 10 * 1024 * 1024) { kfStatus.textContent = file.name + ' too large, skipped'; continue; }
      const thumb = document.createElement('img'); thumb.src = URL.createObjectURL(file); kfPreview.appendChild(thumb);
      try {
        const url = await uploadImageFile(file);
        urls.push(url);
        kfStatus.textContent = 'Uploaded ' + urls.length + '/' + files.length;
      } catch (err) {
        kfStatus.textContent = 'Failed ' + file.name + ': ' + err.message;
      }
    }
    if (urls.length) {
      const existing = kfTextarea.value.split('\n').map(s=>s.trim()).filter(Boolean);
      const all = existing.concat(urls);
      kfTextarea.value = all.join('\n');
      kfStatus.textContent = 'Added ' + urls.length + ' URL(s) ✓';
      parse();
    }
  }

  document.getElementById('btnCheckUrl').onclick = async () => {
    const url = document.getElementById('imageUrl').value.trim();
    const status = document.getElementById('urlStatus');
    if (!url) { status.textContent = 'enter a URL first'; return; }
    try {
      const res = await FB.post('/api/validate-url', { url: url });
      status.textContent = res.ok ? 'valid public URL ✓' : 'invalid: ' + res.detail;
    } catch (e) { status.textContent = 'error: ' + e.message; }
  };

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
      media_type: 'video',
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
    ['#', 'Prompt', 'Type', 'Duration', 'FPS'].forEach((h) =>
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
      tr.appendChild(FB.el('td', {}, s.duration || '-'));
      tr.appendChild(FB.el('td', {}, s.fps || '-'));
      tbody.appendChild(tr);
    });
    tbl.appendChild(tbody);
    previewWrap.appendChild(tbl);
  }

  document.getElementById('btnCreate').onclick = async () => {
    if (!parsedJobs.length) { await parse(); }
    if (!parsedJobs.length) { alert('No valid prompts.'); return; }
    const name = document.getElementById('batchName').value.trim() || 'Video batch';
    const maxAttempts = parseInt(document.getElementById('maxAttempts').value, 10) || 3;
    const body = {
      name: name,
      media_type: 'video',
      jobs: parsedJobs.map((j, i) => ({
        job_index: i,
        type: j.type,
        prompt: j.prompt,
        negative_prompt: j.negative_prompt || null,
        provider: j.provider,
        model: j.model,
        fallback_provider: null,
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
