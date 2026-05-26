// =============================================================================
// reports.js — Reports tab: generate, display, and history
// Uses only existing CSS variables and class names from dist/css/main.css
// =============================================================================

const REPORT_LABELS = {
  daily_agenda:      '📅 Что у меня сегодня?',
  completed_review:  '✅ Обзор выполненных задач',
  weekly_review:     '📊 Недельный обзор',
};

// ---------------------------------------------------------------------------
// DOM helpers
// ---------------------------------------------------------------------------

function $(id) { return document.getElementById(id); }

function setVisible(el, visible) {
  if (el) el.style.display = visible ? '' : 'none';
}

/** Render a date string in a human-readable format. */
function fmtDate(iso) {
  try {
    return new Date(iso).toLocaleString('ru-RU', {
      day: '2-digit', month: '2-digit', year: 'numeric',
      hour: '2-digit', minute: '2-digit',
    });
  } catch {
    return iso;
  }
}

// ---------------------------------------------------------------------------
// API calls  (no httpx / external libs — plain fetch)
// ---------------------------------------------------------------------------

async function apiGenerateReport(reportType, targetDate) {
  const body = { report_type: reportType };
  if (targetDate) body.target_date = targetDate;
  const res = await fetch('/api/v1/reports/generate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

async function apiListReports() {
  const res = await fetch('/api/v1/reports');
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const data = await res.json();
  // Backend returns a list directly; guard against wrapped { reports: [] } shape too
  return Array.isArray(data) ? data : (data.reports || []);
}

// ---------------------------------------------------------------------------
// Rendering
// ---------------------------------------------------------------------------

function renderPreview(record) {
  const preview   = $('reports-preview');
  const titleEl   = $('reports-preview-title');
  const dateEl    = $('reports-preview-date');
  const contentEl = $('reports-preview-content');

  if (!preview) return;
  titleEl.textContent   = REPORT_LABELS[record.type] || record.type;
  dateEl.textContent    = fmtDate(record.generated_at);
  contentEl.textContent = record.content;
  setVisible(preview, true);
  preview.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

function renderHistoryItem(record) {
  const item = document.createElement('div');
  item.className = 'vault__item';
  item.style.cssText = 'cursor:pointer;display:flex;align-items:flex-start;gap:12px;';

  const left = document.createElement('div');
  left.style.flex = '1';

  const title = document.createElement('div');
  title.style.cssText = 'font-weight:600;color:var(--color-text);font-size:14px';
  title.textContent = REPORT_LABELS[record.type] || record.type;

  const meta = document.createElement('div');
  meta.style.cssText = 'font-size:12px;color:var(--color-text-muted);margin-top:4px';
  meta.textContent = fmtDate(record.generated_at)
    + (record.target_date ? ` · за ${record.target_date}` : '')
    + (record.vault_scope_ids?.length ? ` · ${record.vault_scope_ids.length} объектов` : '');

  const preview = document.createElement('div');
  preview.style.cssText = 'font-size:13px;color:var(--color-text-faint);margin-top:4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:480px';
  preview.textContent = record.content.slice(0, 120);

  left.append(title, meta, preview);
  item.appendChild(left);

  item.addEventListener('click', () => renderPreview(record));
  return item;
}

async function loadHistory() {
  const list = $('reports-history-list');
  if (!list) return;
  list.innerHTML = '<div style="font-size:13px;color:var(--color-text-muted);padding:8px 0">Загрузка…</div>';
  try {
    const records = await apiListReports();
    list.innerHTML = '';
    if (!records.length) {
      list.innerHTML = '<div style="font-size:13px;color:var(--color-text-muted);padding:8px 0">История пуста</div>';
      return;
    }
    records.forEach(r => list.appendChild(renderHistoryItem(r)));
  } catch (err) {
    list.innerHTML = `<div style="font-size:13px;color:var(--danger);padding:8px 0">Ошибка загрузки: ${err.message}</div>`;
  }
}

// ---------------------------------------------------------------------------
// Generate flow
// ---------------------------------------------------------------------------

async function handleGenerate(reportType, showToast) {
  const spinner = $('reports-spinner');
  const preview = $('reports-preview');

  setVisible(spinner, true);
  setVisible(preview, false);

  try {
    const record = await apiGenerateReport(reportType, null);
    renderPreview(record);
    await loadHistory();
    showToast?.('Отчёт сгенерирован', 'success');
  } catch (err) {
    showToast?.(`Ошибка генерации: ${err.message}`, 'error');
  } finally {
    setVisible(spinner, false);
  }
}

// ---------------------------------------------------------------------------
// Public init
// ---------------------------------------------------------------------------

export function initReports({ showToast } = {}) {
  // Wire template card clicks
  document.querySelectorAll('[data-report-type]').forEach(card => {
    card.addEventListener('click', () => {
      handleGenerate(card.dataset.reportType, showToast);
    });
  });

  // Refresh button
  $('reports-refresh-btn')?.addEventListener('click', loadHistory);

  // Load history on first activation of the tab
  const tabBtn = document.querySelector('.nav__item[data-tab="reports"]');
  if (tabBtn) {
    tabBtn.addEventListener('click', loadHistory, { once: true });
  }
}
