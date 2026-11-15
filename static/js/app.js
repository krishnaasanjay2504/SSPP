// SSPP shared front-end behaviour
(function () {
  const root = document.documentElement;
  const btn = document.getElementById('themeToggle');
  const setIcon = () => { if (btn) btn.innerHTML = root.getAttribute('data-bs-theme') === 'dark' ? '<i class="bi bi-sun"></i>' : '<i class="bi bi-moon-stars"></i>'; };
  setIcon();
  if (btn) btn.addEventListener('click', () => {
    const t = root.getAttribute('data-bs-theme') === 'dark' ? 'light' : 'dark';
    root.setAttribute('data-bs-theme', t);
    try { localStorage.setItem('sspp-theme', t); } catch (e) {}
    setIcon();
    document.dispatchEvent(new Event('themechange'));
  });

  // notifications dropdown (lazy)
  const nb = document.getElementById('notifBtn');
  if (nb) nb.addEventListener('show.bs.dropdown', async () => {
    const list = document.getElementById('notifList');
    try {
      const r = await fetch('/api/notifications');
      const rows = await r.json();
      list.innerHTML = rows.length ? rows.map(n => `<a class="notif-item ${n.is_read ? '' : 'unread'}" href="/notifications/open/${n.id}">
        <div class="fw-semibold small">${esc(n.title)}</div><div class="small text-muted">${esc(n.message)}</div>
        <div class="text-muted" style="font-size:.7rem">${n.created_at}</div></a>`).join('')
        : '<div class="p-4 text-center text-muted small"><i class="bi bi-bell-slash d-block fs-3 mb-1"></i>You are all caught up</div>';
    } catch (e) { list.innerHTML = '<div class="p-3 small text-danger">Could not load notifications</div>'; }
  });

  // confirm buttons
  document.addEventListener('submit', (e) => {
    const m = e.target.getAttribute('data-confirm');
    if (m && !confirm(m)) e.preventDefault();
  });

  // tooltips
  document.querySelectorAll('[data-bs-toggle="tooltip"]').forEach(el => new bootstrap.Tooltip(el));
})();

function esc(s) { const d = document.createElement('div'); d.textContent = s ?? ''; return d.innerHTML; }

// Chart defaults that follow the theme
window.ssppChartColors = ['#4f46e5', '#a855f7', '#ec4899', '#f59e0b', '#10b981', '#0ea5e9', '#ef4444', '#64748b', '#14b8a6', '#8b5cf6'];
if (window.Chart) {
  Chart.defaults.font.family = 'Inter, system-ui, sans-serif';
  Chart.defaults.plugins.legend.labels.usePointStyle = true;
  const applyTheme = () => {
    const dark = document.documentElement.getAttribute('data-bs-theme') === 'dark';
    Chart.defaults.color = dark ? '#cbd5e1' : '#475569';
    Chart.defaults.borderColor = dark ? 'rgba(255,255,255,.08)' : 'rgba(0,0,0,.06)';
  };
  applyTheme();
  document.addEventListener('themechange', () => { applyTheme(); Object.values(Chart.instances).forEach(c => c.update()); });
}
