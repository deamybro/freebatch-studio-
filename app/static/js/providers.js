(async function () {
  const grid = document.getElementById('providersGrid');
  const banner = document.getElementById('pricingBanner');

  async function refresh() {
    try {
      const d = await FB.api('/api/providers');
      banner.innerHTML = '';
      if (d.pricing_warning) {
        banner.appendChild(FB.el('div', { class: 'alert alert-warn' }, '⚠ ' + d.pricing_warning));
      }
      grid.innerHTML = '';
      d.providers.forEach((p) => {
        grid.appendChild(card(p));
      });
    } catch (e) {
      grid.innerHTML = '<span class="muted">' + e.message + '</span>';
    }
  }

  function card(p) {
    const el = FB.el('div', { class: 'card' });
    const head = FB.el('div', { style: 'display:flex;justify-content:space-between;align-items:center' });
    head.appendChild(FB.el('h2', {}, p.display_name));
    head.appendChild(p.enabled ? FB.el('span', { class: 'badge badge-ok' }, 'enabled')
      : FB.el('span', { class: 'badge badge-muted' }, 'disabled'));
    el.appendChild(head);

    const kv = FB.el('div', { class: 'kv' });
    kv.appendChild(FB.el('div', { class: 'k' }, 'Configured'));
    kv.appendChild(FB.el('div', { class: 'v' }, p.configured ? 'yes' : 'no'));
    kv.appendChild(FB.el('div', { class: 'k' }, 'Key'));
    kv.appendChild(FB.el('div', { class: 'v' }, p.secrets.agn_configured));
    kv.appendChild(FB.el('div', { class: 'k' }, 'Media'));
    kv.appendChild(FB.el('div', { class: 'v' }, p.media_types.join(', ')));
    if (p.health) {
      kv.appendChild(FB.el('div', { class: 'k' }, 'Health'));
      kv.appendChild(FB.el('div', { class: 'v' }, p.health.state + ' — ' + p.health.detail));
    } else {
      kv.appendChild(FB.el('div', { class: 'k' }, 'Health'));
      kv.appendChild(FB.el('div', { class: 'v' }, 'not checked yet'));
    }
    el.appendChild(kv);

    if (p.capabilities && p.capabilities.note) {
      el.appendChild(FB.el('div', { class: 'muted', style: 'margin-top:8px;font-size:12px' }, p.capabilities.note));
    }
    if (p.capabilities && p.capabilities.anonymous_note) {
      el.appendChild(FB.el('div', { class: 'alert alert-warn', style: 'margin-top:8px' }, p.capabilities.anonymous_note));
    }

    if (p.pricing && p.pricing.length) {
      const pricing = FB.el('div', { class: 'kv', style: 'margin-top:10px' });
      pricing.appendChild(FB.el('div', { class: 'k' }, 'Pricing status'));
      pricing.appendChild(FB.el('div', { class: 'v' }, p.pricing[0].pricing_status));
      pricing.appendChild(FB.el('div', { class: 'k' }, 'Expected price'));
      pricing.appendChild(FB.el('div', { class: 'v' }, p.pricing[0].expected_price || '-'));
      pricing.appendChild(FB.el('div', { class: 'k' }, 'Last verified'));
      pricing.appendChild(FB.el('div', { class: 'v' }, FB.fmtTime(p.pricing[0].last_verified) || 'never'));
      pricing.appendChild(FB.el('div', { class: 'k' }, 'Docs'));
      const doc = FB.el('div', { class: 'v' });
      if (p.pricing[0].doc_label) {
        const a = FB.el('a', { href: p.pricing[0].doc_label, target: '_blank', rel: 'noopener' }, 'official documentation');
        doc.appendChild(a);
      } else { doc.textContent = '—'; }
      pricing.appendChild(doc);
      el.appendChild(pricing);
    }

    const actions = FB.el('div', { class: 'job-actions', style: 'margin-top:12px' });
    const healthBtn = FB.el('button', { class: 'btn btn-small' }, 'Check health');
    healthBtn.onclick = async () => {
      try { await FB.post('/api/providers/' + p.name + '/health'); refresh(); }
      catch (e) { alert(e.message); }
    };
    actions.appendChild(healthBtn);
    if (p.name === 'agnes_image' || p.name === 'agnes_video') {
      const verifyBtn = FB.el('button', { class: 'btn btn-small btn-primary' }, 'Verify key (1 free gen)');
      verifyBtn.onclick = async () => {
        if (!confirm('This runs ONE minimal free generation to verify the API key. Continue?')) return;
        verifyBtn.disabled = true;
        verifyBtn.textContent = 'Verifying...';
        try {
          const res = await FB.post('/api/providers/' + p.name + '/verify');
          alert('Verification: ' + res.state + ' — ' + res.detail);
        } catch (e) { alert('Verify failed: ' + e.message); }
        verifyBtn.disabled = false;
        verifyBtn.textContent = 'Verify key (1 free gen)';
        refresh();
      };
      actions.appendChild(verifyBtn);
    }
    if (p.name === 'ai_horde') {
      const modelsBtn = FB.el('button', { class: 'btn btn-small' }, 'Fetch active models');
      modelsBtn.onclick = async () => {
        try {
          const m = await FB.api('/api/providers/ai_horde/models');
          alert('Active models (' + m.models.length + '):\n' +
            m.models.slice(0, 40).map((x) => x.name + ' (workers ' + x.workers + ')').join('\n'));
        } catch (e) { alert('Could not fetch models: ' + e.message); }
      };
      actions.appendChild(modelsBtn);
    }
    if (!p.enabled && (p.name === 'wangp' || p.name === 'comfyui')) {
      const note = FB.el('div', { class: 'alert alert-info', style: 'margin-top:10px' },
        'Not configured — intended for future local GPU hardware.');
      el.appendChild(note);
    }
    el.appendChild(actions);
    return el;
  }

  refresh();
  setInterval(refresh, 20000);
})();