(async function () {
  const out = document.getElementById('logOutput');
  const auto = document.getElementById('autoRefresh');
  let timer = null;

  async function refresh() {
    try {
      const d = await FB.api('/api/logs?limit=500');
      out.textContent = d.lines.join('\n');
      out.scrollTop = out.scrollHeight;
    } catch (e) {
      out.textContent = 'error: ' + e.message;
    }
  }

  document.getElementById('btnRefresh').onclick = refresh;
  auto.onchange = () => {
    if (timer) { clearInterval(timer); timer = null; }
    if (auto.checked) timer = setInterval(refresh, 5000);
  };

  refresh();
  if (auto.checked) timer = setInterval(refresh, 5000);
})();