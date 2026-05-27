// =============================================================================
// app.js — SPA entrypoint: router, nav wiring, server status, theme toggle
// =============================================================================
import { api } from './api.js?v=20260520153419';
import { initChat }     from './chat.js?v=20260526131517';
import { initVault }    from './vault.js?v=20260520153419';
import { initProjects } from './projects.js?v=20260520153419';
import { initSearch }   from './search.js?v=20260526131517';
import { initSettings } from './settings.js?v=20260520153419';
import { initRules }    from './rules.js?v=20260527151156';
import { initReports }  from './reports.js?v=20260520153419';
import { initInbox }    from './inbox.js?v=20260527154107';
import { initToday }    from './today.js?v=20260526121929';

// ── Toast ────────────────────────────────────────────────────────────────────
export function showToast(msg, type = 'info') {
  const container = document.getElementById('toast-container');
  if (!container) return;
  const el = document.createElement('div');
  el.className = `toast toast--${type}`;
  el.textContent = msg;
  container.appendChild(el);
  setTimeout(() => {
    el.style.opacity = '0';
    el.style.transition = 'opacity .3s';
    setTimeout(() => el.remove(), 350);
  }, 3500);
}

// ── Tab routing ──────────────────────────────────────────────────────────────
const TABS = ['today', 'inbox', 'chat', 'vault', 'projects', 'rules', 'search', 'reports', 'settings'];
let _currentTab = 'chat';

function activateTab(tabId) {
  if (!TABS.includes(tabId)) tabId = 'chat';
  _currentTab = tabId;

  // Panels
  document.querySelectorAll('.tab-panel').forEach(el => {
    el.classList.toggle('tab-panel--active', el.dataset.tab === tabId);
  });

  // Nav items
  document.querySelectorAll('.nav__item[data-tab]').forEach(btn => {
    btn.classList.toggle('nav__item--active', btn.dataset.tab === tabId);
  });

  // Update hash without triggering hashchange loop
  const newHash = '#' + tabId;
  if (location.hash !== newHash) history.replaceState(null, '', newHash);
}

function tabFromHash() {
  const h = location.hash.replace('#', '');
  return TABS.includes(h) ? h : 'chat';
}

// ── Server status polling ────────────────────────────────────────────────────
const statusDot  = document.getElementById('status-dot');
const statusText = document.getElementById('status-text');

function setStatus(ok, text) {
  if (statusDot) {
    statusDot.className = 'nav__status-dot ' + (ok ? 'nav__status-dot--ok' : 'nav__status-dot--err');
  }
  if (statusText) statusText.textContent = text;
}

// ── Nav sys-status (MLX + sync times) ───────────────────────────────────────
const navMlxDot   = document.getElementById('nav-mlx-dot');
const navMlxLabel = document.getElementById('nav-mlx-label');
const navCalLabel = document.getElementById('nav-cal-label');
const navMailLabel= document.getElementById('nav-mail-label');

function updateNavSysStatus(statusData, syncData) {
  // MLX traffic light
  if (navMlxDot && navMlxLabel) {
    const mlxAvailable = statusData?.mlx_available;
    const modelLoaded  = statusData?.model_loaded;
    navMlxDot.className = 'nav__sys-dot ' + (
      modelLoaded  ? 'nav__sys-dot--ok'   :
      mlxAvailable ? 'nav__sys-dot--warn' :
                     'nav__sys-dot--err'
    );
    navMlxLabel.textContent = modelLoaded
      ? (statusData?.model_name || 'MLX готов')
      : mlxAvailable
        ? 'Модель не загружена'
        : 'MLX нет';
  }

  // Per-source last sync times
  const perSource = syncData?.last_sync_per_source || {};
  if (navCalLabel)  navCalLabel.textContent  = 'Cal '  + (perSource.calendar || '—');
  if (navMailLabel) navMailLabel.textContent = 'Mail ' + (perSource.mail     || '—');
}

async function pollStatus() {
  try {
    const data = await api.status();
    setStatus(true, data.status || 'Online');
    // Fetch sync status in parallel
    const syncData = await api.syncStatus().catch(() => ({}));
    updateNavSysStatus(data, syncData);
  } catch {
    setStatus(false, 'Офлайн');
    updateNavSysStatus(null, null);
  }
}

// ── Theme toggle ─────────────────────────────────────────────────────────────
function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  localStorage.setItem('pa_theme', theme);
  const btn = document.getElementById('theme-toggle');
  if (btn) btn.innerHTML = theme === 'dark'
    ? `<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" width="14" height="14"><path stroke-linecap="round" stroke-linejoin="round" d="M12 3v2.25m6.364.386l-1.591 1.591M21 12h-2.25m-.386 6.364l-1.591-1.591M12 18.75V21m-4.773-4.227l-1.591 1.591M5.25 12H3m4.227-4.773L5.636 5.636M15.75 12a3.75 3.75 0 11-7.5 0 3.75 3.75 0 017.5 0z"/></svg> Светлая тема`
    : `<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" width="14" height="14"><path stroke-linecap="round" stroke-linejoin="round" d="M21.752 15.002A9.718 9.718 0 0118 15.75c-5.385 0-9.75-4.365-9.75-9.75 0-1.33.266-2.597.748-3.752A9.753 9.753 0 003 11.25C3 16.635 7.365 21 12.75 21a9.753 9.753 0 009.002-5.998z"/></svg> Тёмная тема`;
}

// ── Init ─────────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  // Theme
  const savedTheme = localStorage.getItem('pa_theme') || 'light';
  applyTheme(savedTheme);
  document.getElementById('theme-toggle')?.addEventListener('click', () => {
    const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
    applyTheme(next);
  });

  // Nav wiring
  document.querySelectorAll('.nav__item[data-tab]').forEach(btn => {
    btn.addEventListener('click', () => {
      activateTab(btn.dataset.tab);
    });
  });

  // Hash routing
  window.addEventListener('hashchange', () => activateTab(tabFromHash()));
  activateTab(tabFromHash());

  // Shared context passed to tab modules
  const ctx = { showToast, activateTab };

  // Init all tabs
  initToday(ctx);
  initInbox(ctx);
  initChat(ctx);
  initVault(ctx);
  initProjects(ctx);
  initSearch(ctx);
  initSettings(ctx);
  initRules(ctx);
  initReports(ctx);

  // Load user profile for nav block
  api.profileGet().then(p => {
    const nameEl  = document.getElementById('nav-user-name');
    const avatarEl = document.getElementById('nav-user-avatar');
    if (p?.name && nameEl)  nameEl.textContent  = p.name;
    if (p?.name && avatarEl) avatarEl.textContent = p.name.trim().slice(0, 1).toUpperCase() || 'И';
  }).catch(() => {});

  // Status polling
  pollStatus();
  setInterval(pollStatus, 30_000);
});
