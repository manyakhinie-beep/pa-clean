// =============================================================================
// vault.js — Vault browser: sidebar filters, list, detail panel, mentioned-in
// =============================================================================
import { api } from './api.js?v=20260520153419';

// Tool definitions per section/type
const VAULT_TOOLS = {
  mail: [
    { id: 'draft',       label: '✉️ Ответить',       icon: '↩',  mode: 'draft',     message: '/draft ',    title: 'Написать ответ' },
    { id: 'summarize',   label: '📝 Суммаризировать', icon: '∑',  mode: 'summarize', message: '/summarize ',title: 'Суммаризировать письма по теме' },
    { id: 'reclassify',  label: '🏷 Классифицировать',icon: '#',  mode: 'reclassify',message: '',           title: 'Переклассифицировать' },
  ],
  calendar: [
    { id: 'summarize',   label: '📝 Суммаризировать', icon: '∑',  mode: 'summarize', message: '/summarize ',title: 'Суммаризировать встречу' },
    { id: 'chat',        label: '💬 Обсудить',         icon: '💬', mode: 'chat',      message: 'Расскажи подробнее о встрече и что нужно подготовить.', title: 'Обсудить встречу' },
    { id: 'reclassify',  label: '🏷 Классифицировать',icon: '#',  mode: 'reclassify',message: '',           title: 'Переклассифицировать' },
  ],
  default: [
    { id: 'summarize',   label: '📝 Суммаризировать', icon: '∑',  mode: 'summarize', message: '/summarize ',title: 'Суммаризировать документ' },
    { id: 'chat',        label: '💬 Обсудить',         icon: '💬', mode: 'chat',      message: '',           title: 'Обсудить документ' },
    { id: 'reclassify',  label: '🏷 Классифицировать',icon: '#',  mode: 'reclassify',message: '',           title: 'Переклассифицировать' },
  ],
};

// Urgency / category display config
const URGENCY_CONFIG = {
  urgent:    { label: 'Срочно',   cls: 'urgent' },
  important: { label: 'Важно',    cls: 'important' },
  low:       { label: 'Низкое',   cls: 'low' },
};
const CATEGORY_CONFIG = {
  finance:  { label: 'Финансы',  cls: 'finance' },
  meetings: { label: 'Встречи',  cls: 'meetings' },
  projects: { label: 'Проекты',  cls: 'projects' },
  hr:       { label: 'HR',       cls: 'hr' },
  legal:    { label: 'Правовые', cls: 'legal' },
  travel:   { label: 'Поездки',  cls: 'travel' },
  other:    { label: 'Другое',   cls: 'other' },
};

/** Normalize a date string (handles "2026-05-10 15:00:00" format from PyYAML). */
function normDateStr(d) {
  if (!d) return '';
  return d.replace(' ', 'T');
}

/** Safe HTML escape */
function escHtml(s) {
  return String(s || '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

/** Strip markdown for plain-text previews */
function stripMd(s) {
  let t = String(s || '');
  t = t.replace(/^\[[^\]]*\]\s*/, '');
  t = t.replace(/^#{1,6}\s+/gm, '');
  t = t.replace(/^>\s*/gm, '');
  t = t.replace(/\*{1,3}([^*\n]+)\*{1,3}/g, '$1');
  t = t.replace(/_{1,3}([^_\n]+)_{1,3}/g, '$1');
  t = t.replace(/~~([^~\n]+)~~/g, '$1');
  t = t.replace(/`[^`\n]*`/g, '');
  t = t.replace(/\[([^\]]*)\]\([^)]*\)/g, '$1');
  t = t.replace(/\s+/g, ' ').trim();
  return t;
}

function debounce(fn, ms) {
  let t;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}

/** Build CSS class for a tag pill from "urgency:urgent" or "category:finance" */
function tagPillClass(tag) {
  const [ns, val] = tag.split(':');
  if (!val) return 'vault__tag-pill--default';
  return `vault__tag-pill--${ns}-${val}`;
}

/** Format "urgency:urgent" → "Срочно" / "category:finance" → "Финансы" etc. */
function tagPillLabel(tag) {
  const [ns, val] = tag.split(':');
  if (ns === 'urgency' && URGENCY_CONFIG[val]) return URGENCY_CONFIG[val].label;
  if (ns === 'category' && CATEGORY_CONFIG[val]) return CATEGORY_CONFIG[val].label;
  return tag;
}

export function initVault(ctx) {
  const { showToast, activateTab } = ctx;

  // Center panel refs
  const listEl          = document.getElementById('vault-list');
  const searchEl        = document.getElementById('vault-search');
  const sortEl          = document.getElementById('vault-sort');
  const reloadBtn       = document.getElementById('vault-reload-btn');

  // Viewer refs
  const viewerEmpty     = document.getElementById('vault-viewer-empty');

  // Breadcrumb
  const breadcrumbEl    = document.getElementById('vault-breadcrumb');
  const breadcrumbPath  = document.getElementById('vault-breadcrumb-path');

  // Detail panel refs
  const detailPanel     = document.getElementById('vault-detail');
  const detailTitle     = document.getElementById('vault-detail-title');
  const detailTagPills  = document.getElementById('vault-detail-tag-pills');
  const detailMetaLine  = document.getElementById('vault-detail-meta-line');
  const detailContent   = document.getElementById('vault-detail-content');
  const detailClose     = document.getElementById('vault-detail-close');
  const detailEdit      = document.getElementById('vault-detail-edit');
  const detailDelete    = document.getElementById('vault-detail-delete');

  // Sidebar filter groups
  const urgencyFilters  = document.getElementById('vault-urgency-filters');
  const categoryFilters = document.getElementById('vault-category-filters');

  // Mentioned-in panel refs
  const mentionedEmpty   = document.getElementById('vault-mentioned-empty');
  const mentionedContent = document.getElementById('vault-mentioned-content');
  const mentionedHeader  = document.getElementById('vault-mentioned-header');
  const mentionedItems   = document.getElementById('vault-mentioned-items');

  // Legacy tag-filters compat element
  const tagFilters = document.getElementById('vault-tag-filters');

  if (!listEl) return;

  let allDocs           = [];
  let activeSection     = '';
  let activeUrgency     = '';
  let activeCategory    = '';
  let activeTags        = new Set();
  let editingPath       = null;
  let currentRawContent = '';
  let currentOpenPath   = null;

  // ── Load docs + sidebar counts ──────────────────────────────────────────
  async function loadDocs() {
    try {
      const data = await api.vaultList(activeSection, 500);
      allDocs = data.docs || [];

      // Update section counts
      const sc = data.section_counts || {};
      const total = data.total_all ?? allDocs.length;
      _setCount('vsec-count-all',      total);
      _setCount('vsec-count-calendar', sc.calendar || 0);
      _setCount('vsec-count-mail',     sc.mail || 0);
      _setCount('vsec-count-contacts', sc.contacts || 0);

      // Render urgency / category sidebar filters
      renderUrgencyFilters(data.urgency_counts || {});
      renderCategoryFilters(data.category_counts || {});

      renderList();
    } catch (err) {
      showToast('Ошибка загрузки vault: ' + err.message, 'error');
    }
  }

  function _setCount(id, n) {
    const el = document.getElementById(id);
    if (el) el.textContent = n;
  }

  // ── Sidebar section buttons ──────────────────────────────────────────────
  document.querySelectorAll('.vault__sidebar-item[data-section]').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.vault__sidebar-item[data-section]').forEach(b =>
        b.classList.remove('vault__sidebar-item--active'));
      btn.classList.add('vault__sidebar-item--active');
      activeSection = btn.dataset.section;
      activeUrgency = '';
      activeCategory = '';
      activeTags.clear();
      loadDocs();
    });
  });

  // ── Urgency filters ──────────────────────────────────────────────────────
  function renderUrgencyFilters(counts) {
    if (!urgencyFilters) return;
    // Keep label header, remove existing dot-items
    const label = urgencyFilters.querySelector('.vault__filter-group-label');
    urgencyFilters.innerHTML = '';
    if (label) urgencyFilters.appendChild(label);

    const hasAny = Object.values(counts).some(v => v > 0);
    if (!hasAny) return;

    Object.entries(URGENCY_CONFIG).forEach(([key, cfg]) => {
      const count = counts[key] || 0;
      if (!count) return;
      const btn = document.createElement('button');
      btn.className = 'vault__dot-item' + (activeUrgency === key ? ' vault__dot-item--active' : '');
      btn.dataset.urgency = key;
      btn.innerHTML = `
        <span class="vault__dot vault__dot--${cfg.cls}"></span>
        <span class="vault__dot-label">${escHtml(cfg.label)}</span>
        <span class="vault__dot-count">${count}</span>
      `;
      btn.addEventListener('click', () => {
        activeUrgency = (activeUrgency === key) ? '' : key;
        activeCategory = '';
        activeTags.clear();
        renderUrgencyFilters(counts);
        renderCategoryFilters(lastCategoryCounts);
        renderList();
      });
      urgencyFilters.appendChild(btn);
    });
  }

  let lastCategoryCounts = {};

  function renderCategoryFilters(counts) {
    lastCategoryCounts = counts;
    if (!categoryFilters) return;
    const label = categoryFilters.querySelector('.vault__filter-group-label');
    categoryFilters.innerHTML = '';
    if (label) categoryFilters.appendChild(label);

    const hasAny = Object.values(counts).some(v => v > 0);
    if (!hasAny) return;

    Object.entries(CATEGORY_CONFIG).forEach(([key, cfg]) => {
      const count = counts[key] || 0;
      if (!count) return;
      const btn = document.createElement('button');
      btn.className = 'vault__dot-item' + (activeCategory === key ? ' vault__dot-item--active' : '');
      btn.dataset.category = key;
      btn.innerHTML = `
        <span class="vault__dot vault__dot--${cfg.cls}"></span>
        <span class="vault__dot-label">${escHtml(cfg.label)}</span>
        <span class="vault__dot-count">${count}</span>
      `;
      btn.addEventListener('click', () => {
        activeCategory = (activeCategory === key) ? '' : key;
        activeUrgency = '';
        activeTags.clear();
        renderUrgencyFilters(lastUrgencyCounts);
        renderCategoryFilters(counts);
        renderList();
      });
      categoryFilters.appendChild(btn);
    });
  }

  let lastUrgencyCounts = {};

  // Patch renderUrgencyFilters to also save counts
  const _origRenderUrgency = renderUrgencyFilters;
  // (inline below after both defs)

  // ── Tags (legacy compat from settings tab) ───────────────────────────────
  async function loadTags() {
    try {
      const data = await api.vaultTags();
      renderTagFilters(data.tags || []);
    } catch { /* ignore */ }
  }

  function renderTagFilters(tags) {
    if (!tagFilters) return;
    tagFilters.innerHTML = '';
    tags.slice(0, 20).forEach(tag => {
      const btn = document.createElement('button');
      btn.className = 'vault__filter-btn' + (activeTags.has(tag) ? ' vault__filter-btn--active' : '');
      btn.dataset.tag = tag;
      btn.textContent = tag;
      btn.addEventListener('click', () => {
        if (activeTags.has(tag)) activeTags.delete(tag);
        else activeTags.add(tag);
        btn.classList.toggle('vault__filter-btn--active', activeTags.has(tag));
        activeUrgency = '';
        activeCategory = '';
        renderList();
      });
      tagFilters.appendChild(btn);
    });
  }

  // ── Filtering & list rendering ────────────────────────────────────────────
  function getFilteredDocs() {
    const q = (searchEl?.value || '').toLowerCase();
    let docs = allDocs;

    // Urgency filter
    if (activeUrgency) {
      docs = docs.filter(d => (d.tags || []).includes(`urgency:${activeUrgency}`));
    }
    // Category filter
    if (activeCategory) {
      docs = docs.filter(d => (d.tags || []).includes(`category:${activeCategory}`));
    }
    // Tag filter (legacy)
    if (activeTags.size) {
      docs = docs.filter(d => {
        const tags = d.tags || [];
        return [...activeTags].some(t => tags.includes(t));
      });
    }
    // Text search
    if (q) {
      docs = docs.filter(d =>
        (d.title || '').toLowerCase().includes(q) ||
        (d.snippet || '').toLowerCase().includes(q) ||
        (d.path || '').toLowerCase().includes(q)
      );
    }
    // Sort
    const sortBy = sortEl?.value || 'date';
    if (sortBy === 'title') {
      docs = [...docs].sort((a, b) => (a.title || '').localeCompare(b.title || ''));
    } else {
      docs = [...docs].sort((a, b) => normDateStr(b.date).localeCompare(normDateStr(a.date)));
    }
    return docs;
  }

  // ── Mail thread grouping ──────────────────────────────────────────────────
  function groupByThread(mailDocs) {
    const threads = new Map();
    const noThread = [];
    mailDocs.forEach(doc => {
      const tid = doc.thread_id || '';
      if (!tid) { noThread.push(doc); return; }
      if (!threads.has(tid)) threads.set(tid, []);
      threads.get(tid).push(doc);
    });
    threads.forEach(msgs => msgs.sort((a, b) => normDateStr(a.date).localeCompare(normDateStr(b.date))));
    return { threads, noThread };
  }

  function renderGenericItem(doc) {
    const el = document.createElement('div');
    el.className = 'vault__list-item' + (doc.path === currentOpenPath ? ' vault__list-item--selected' : '');

    const icon = doc.section === 'calendar' ? '📅' : doc.section === 'contacts' ? '👤' : '📄';
    const tags = (doc.tags || []).slice(0, 2)
      .map(t => `<span class="vault__tag-pill ${tagPillClass(t)}">${escHtml(tagPillLabel(t))}</span>`).join('');

    el.innerHTML = `
      <span class="vault__item-icon">${icon}</span>
      <div class="vault__item-body">
        <div class="vault__item-title">${escHtml(doc.title || doc.path)}</div>
        <div class="vault__item-meta">${escHtml(stripMd(doc.snippet || '').slice(0, 80))}${tags}</div>
      </div>
      <span class="vault__item-date">${escHtml((doc.date || '').slice(0, 10))}</span>
    `;
    el.addEventListener('click', () => openDetail(doc.path, doc));
    return el;
  }

  function renderMailItem(doc) {
    const el = document.createElement('div');
    el.className = 'vault__list-item vault__list-item--mail' +
      (doc.path === currentOpenPath ? ' vault__list-item--selected' : '');

    const emailRaw = (doc.from_email || doc.sender || '')
      .replace(/\[\[contacts\//g, '').replace(/\]\]/g, '');
    const senderDisplay = doc.sender_name || emailRaw || '—';
    const snippet = escHtml(stripMd(doc.snippet || '').slice(0, 80));
    const tags = (doc.tags || []).slice(0, 2)
      .map(t => `<span class="vault__tag-pill ${tagPillClass(t)}">${escHtml(tagPillLabel(t))}</span>`).join('');

    el.innerHTML = `
      <span class="vault__item-icon">✉️</span>
      <div class="vault__item-body">
        <div class="vault__item-title">${escHtml(doc.title || doc.path)}</div>
        <div class="vault__item-meta">${escHtml(senderDisplay)}${snippet ? ` · ${snippet}` : ''}${tags ? ' ' + tags : ''}</div>
      </div>
      <span class="vault__item-date">${escHtml((doc.date || '').slice(0, 10))}</span>
    `;
    el.addEventListener('click', () => openDetail(doc.path, doc));
    return el;
  }

  function renderThreadGroup(docs) {
    if (docs.length === 1) return renderMailItem(docs[0]);

    const latest     = docs[docs.length - 1];
    const count      = docs.length;
    const countLabel = count < 2 ? 'письмо' : count < 5 ? 'письма' : 'писем';
    const threadTitle = latest.subject || latest.title || latest.path.split('/').pop();
    const latestDate  = (latest.date || '').slice(0, 10);
    const latestSndr  = latest.sender_name || (latest.from_email || '').replace(/\[\[contacts\//g, '').replace(/\]\]/g, '');
    const tags = [...new Set(docs.flatMap(d => d.tags || []))].slice(0, 3)
      .map(t => `<span class="vault__tag-pill ${tagPillClass(t)}">${escHtml(tagPillLabel(t))}</span>`).join('');

    const autoExpand = count <= 10;
    const wrapper = document.createElement('div');
    wrapper.style.cssText = 'border:1px solid var(--color-border);border-radius:10px;margin:0 0 6px;overflow:hidden';

    const header = document.createElement('div');
    header.style.cssText = 'display:flex;flex-direction:column;gap:2px;padding:10px 12px;cursor:pointer;background:var(--color-surface);user-select:none';
    header.innerHTML = `
      <div style="display:flex;align-items:center;gap:8px">
        <span style="font-size:14px">✉️</span>
        <span style="font-size:13px;font-weight:600;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escHtml(threadTitle)}</span>
        <span style="font-size:11px;background:var(--color-primary);color:#fff;padding:1px 8px;border-radius:99px;flex-shrink:0">${count} ${countLabel}</span>
        <span class="vault__thread-chevron" style="font-size:12px;color:var(--color-text-muted);transition:transform .2s;flex-shrink:0;${autoExpand ? 'transform:rotate(180deg)' : ''}">▾</span>
      </div>
      <div style="display:flex;align-items:center;gap:6px;font-size:12px;color:var(--color-text-muted);padding-left:22px">
        ${latestSndr ? `<span style="font-weight:500">${escHtml(latestSndr)}</span>` : ''}
        ${latestDate ? `<span>${latestDate}</span>` : ''}
        ${tags}
      </div>
    `;
    wrapper.appendChild(header);

    const body = document.createElement('div');
    body.style.cssText = `display:${autoExpand ? 'block' : 'none'};border-top:1px solid var(--color-border)`;
    wrapper.appendChild(body);

    let rendered = false;
    function ensureRendered() {
      if (rendered) return;
      const frag = document.createDocumentFragment();
      docs.forEach((doc, idx) => {
        const item = renderMailItem(doc);
        item.style.borderRadius = '0';
        item.style.margin = '0';
        if (idx < docs.length - 1) item.style.borderBottom = '1px solid var(--color-border)';
        frag.appendChild(item);
      });
      body.appendChild(frag);
      rendered = true;
    }
    if (autoExpand) ensureRendered();

    header.addEventListener('click', () => {
      const expanded = body.style.display !== 'none';
      ensureRendered();
      body.style.display = expanded ? 'none' : 'block';
      const chev = header.querySelector('.vault__thread-chevron');
      if (chev) chev.style.transform = expanded ? '' : 'rotate(180deg)';
    });

    return wrapper;
  }

  function renderList() {
    const docs = getFilteredDocs();
    listEl.innerHTML = '';

    if (!docs.length) {
      listEl.innerHTML = `
        <div class="vault__empty">
          <div class="vault__empty-icon">📂</div>
          <div class="vault__empty-text">Нет документов</div>
          <div class="vault__empty-sub">Попробуйте изменить фильтр или запустить синхронизацию</div>
        </div>`;
      return;
    }

    const mailDocs  = docs.filter(d => d.section === 'mail');
    const otherDocs = docs.filter(d => d.section !== 'mail');
    const units = [];

    otherDocs.forEach(doc => units.push({ date: normDateStr(doc.date || ''), el: renderGenericItem(doc) }));

    if (mailDocs.length) {
      const { threads, noThread } = groupByThread(mailDocs);
      threads.forEach(msgs =>
        units.push({ date: normDateStr(msgs[msgs.length - 1].date || ''), el: renderThreadGroup(msgs) })
      );
      noThread.forEach(doc => units.push({ date: normDateStr(doc.date || ''), el: renderMailItem(doc) }));
    }

    const sortBy = sortEl?.value || 'date';
    if (sortBy !== 'title') units.sort((a, b) => b.date.localeCompare(a.date));

    const frag = document.createDocumentFragment();
    units.forEach(u => frag.appendChild(u.el));
    listEl.appendChild(frag);
  }

  // ── Breadcrumb ────────────────────────────────────────────────────────────
  function updateBreadcrumb(path) {
    if (!breadcrumbEl) return;
    if (!path) {
      breadcrumbEl.style.display = 'none';
      return;
    }
    breadcrumbEl.style.display = 'flex';
    if (breadcrumbPath) {
      // Show last 2 path segments
      const parts = path.replace(/\\/g, '/').split('/').filter(Boolean);
      breadcrumbPath.textContent = parts.slice(-2).join(' / ');
    }
  }

  // ── Tag pills ─────────────────────────────────────────────────────────────
  function renderTagPills(tags) {
    if (!detailTagPills) return;
    detailTagPills.innerHTML = '';
    (tags || []).forEach(tag => {
      const pill = document.createElement('span');
      pill.className = `vault__tag-pill ${tagPillClass(tag)}`;
      pill.textContent = tagPillLabel(tag);
      detailTagPills.appendChild(pill);
    });
  }

  // ── Meta line ─────────────────────────────────────────────────────────────
  function renderMetaLine(fm, section) {
    if (!detailMetaLine) return;
    detailMetaLine.innerHTML = '';
    if (!fm || !Object.keys(fm).length) return;

    const parts = [];

    if (section === 'mail') {
      const senderRaw = (fm.sender || fm.from_email || fm.from || '')
        .replace(/\[\[contacts\//g, '').replace(/\]\]/g, '');
      const senderName = fm.sender_name || '';
      const from = senderName ? `${senderName} <${senderRaw}>` : senderRaw;
      if (from) parts.push(`<span class="meta-sender">${escHtml(from)}</span>`);
      if (fm.to) parts.push(`<span class="meta-arrow">→</span><span class="meta-to">${escHtml(fm.to)}</span>`);
    } else if (section === 'calendar') {
      if (fm.organizer) parts.push(`<span class="meta-sender">${escHtml(fm.organizer)}</span>`);
      if (fm.location) parts.push(`<span class="meta-sep">·</span><span>📍 ${escHtml(fm.location)}</span>`);
      if (fm.calendar) parts.push(`<span class="meta-sep">·</span><span>📆 ${escHtml(fm.calendar)}</span>`);
    }

    if (fm.date || fm.start) {
      const dateStr = (fm.date || fm.start || '').slice(0, 16).replace('T', ' ');
      parts.push(`<span class="meta-date">📅 ${escHtml(dateStr)}</span>`);
    }

    detailMetaLine.innerHTML = parts.join(' ');
  }

  // ── Tool bar ──────────────────────────────────────────────────────────────
  function renderToolBar(id, section, title, isThread, replyMsgId = null) {
    const toolBar = document.getElementById('vault-detail-tools');
    if (!toolBar) return;
    toolBar.innerHTML = '';
    const tools = VAULT_TOOLS[section] || VAULT_TOOLS.default;
    tools.forEach(tool => {
      const btn = document.createElement('button');
      btn.className = 'vault__action-btn';
      btn.title = tool.title;
      btn.innerHTML = `<span class="vault__action-btn-icon">${escHtml(tool.icon)}</span> ${escHtml(tool.label.replace(/^[^\s]+\s/, ''))}`;
      btn.addEventListener('click', async () => {
        if (tool.id === 'reclassify') {
          try {
            await api.classifyApply();
            showToast('Классификация запущена', 'success');
            await loadDocs();
          } catch (err) { showToast('Ошибка: ' + err.message, 'error'); }
          return;
        }
        activateTab('chat');
        if (isThread && id) {
          document.dispatchEvent(new CustomEvent('pa:chat-send', {
            detail: { mode: tool.mode, message: tool.message + title,
                      vault_thread_id: id, reply_message_id: replyMsgId || null },
          }));
        } else {
          document.dispatchEvent(new CustomEvent('pa:chat-send', {
            detail: { path: id, title, mode: tool.mode,
                      message: tool.message, reply_message_id: replyMsgId || null },
          }));
        }
      });
      toolBar.appendChild(btn);
    });
  }

  // ── Frontmatter parser ────────────────────────────────────────────────────
  function parseFrontmatter(content) {
    if (!content.startsWith('---')) return {};
    const end = content.indexOf('\n---', 3);
    if (end === -1) return {};
    const lines = content.slice(3, end).split('\n');
    const fm = {};
    for (const line of lines) {
      const sep = line.indexOf(':');
      if (sep === -1) continue;
      const key = line.slice(0, sep).trim();
      const val = line.slice(sep + 1).trim().replace(/^["']|["']$/g, '');
      if (key) fm[key] = val;
    }
    return fm;
  }

  function sectionFromPath(path) {
    if (path.includes('/mail/') || path.includes('\\mail\\')) return 'mail';
    if (path.includes('/calendar/') || path.includes('\\calendar\\')) return 'calendar';
    if (path.includes('/contacts/') || path.includes('\\contacts\\')) return 'contacts';
    return 'default';
  }

  // ── Mentioned-in panel ────────────────────────────────────────────────────
  async function loadMentionedIn(path) {
    if (!mentionedEmpty || !mentionedContent) return;
    try {
      const data = await api.vaultMentionedIn(path);
      renderMentionedIn(data);
    } catch {
      // Fail silently — right panel is supplementary
      showMentionedEmpty();
    }
  }

  function showMentionedEmpty() {
    if (mentionedEmpty) mentionedEmpty.style.display = '';
    if (mentionedContent) mentionedContent.style.display = 'none';
  }

  function renderMentionedIn(data) {
    const items = data?.items || [];
    if (!items.length) { showMentionedEmpty(); return; }

    if (mentionedEmpty) mentionedEmpty.style.display = 'none';
    if (mentionedContent) mentionedContent.style.display = '';

    if (mentionedHeader) {
      mentionedHeader.textContent = `MENTIONED IN · ${items.length}`;
    }

    if (!mentionedItems) return;
    mentionedItems.innerHTML = '';

    const TYPE_CFG = {
      project:    { label: 'Проект',       icon: '🏗', cls: 'project' },
      calendar:   { label: 'Встреча',       icon: '📅', cls: 'calendar' },
      mail_thread:{ label: 'Тред',          icon: '✉️', cls: 'mail' },
      eisenhower: { label: 'Задача',        icon: '📋', cls: 'eisenhower' },
      thread:     { label: 'Тред',          icon: '✉️', cls: 'thread' },
    };

    items.forEach(item => {
      const cfg = TYPE_CFG[item.type] || { label: item.type, icon: '🔗', cls: 'default' };
      const el = document.createElement('div');
      el.className = 'vault__mentioned-item';
      el.innerHTML = `
        <div class="vault__mentioned-icon vault__mentioned-icon--${cfg.cls}">${cfg.icon}</div>
        <div class="vault__mentioned-body">
          <div class="vault__mentioned-type">${cfg.label}</div>
          <div class="vault__mentioned-title">${escHtml(item.title || item.id || '—')}</div>
          ${item.subtitle ? `<div class="vault__mentioned-sub">${escHtml(item.subtitle)}</div>` : ''}
        </div>
      `;
      el.addEventListener('click', () => {
        // Route to appropriate tab
        if (item.type === 'project') {
          activateTab('projects');
          document.dispatchEvent(new CustomEvent('pa:open-project', { detail: { id: item.id } }));
        } else if (item.type === 'calendar') {
          activateTab('vault');
          if (item.path) openDetail(item.path, { section: 'calendar' });
        } else if (item.type === 'mail_thread' || item.type === 'thread') {
          activateTab('chat');
          document.dispatchEvent(new CustomEvent('pa:chat-send', {
            detail: { mode: 'summarize', message: '/summarize ', vault_thread_id: item.id, suppressSend: true },
          }));
        }
      });
      mentionedItems.appendChild(el);
    });

    // Footer chat button
    const footer = mentionedItems.parentElement?.querySelector('.vault__mentioned-footer');
    if (footer && !footer.querySelector('.vault__mentioned-chat-btn')) {
      const btn = document.createElement('button');
      btn.className = 'vault__mentioned-chat-btn';
      btn.innerHTML = '💬 Обсудить в чате';
      btn.addEventListener('click', () => {
        if (!currentOpenPath) return;
        activateTab('chat');
        document.dispatchEvent(new CustomEvent('pa:chat-send', {
          detail: { path: currentOpenPath, mode: 'chat', message: '', suppressSend: true },
        }));
      });
      footer.innerHTML = '';
      footer.appendChild(btn);
    }
  }

  // ── Detail panel ──────────────────────────────────────────────────────────
  async function openDetail(path, doc) {
    try {
      currentOpenPath = path;
      updateBreadcrumb(path);
      const section = doc?.section || sectionFromPath(path) || 'default';
      const tid = doc?.thread_id;

      // Show detail panel, hide viewer empty state
      if (viewerEmpty) viewerEmpty.style.display = 'none';
      if (detailPanel) detailPanel.style.display = 'flex';

      // Kick off mentioned-in load (non-blocking)
      loadMentionedIn(path);

      // Mail with thread_id — load thread view
      if (section === 'mail' && tid) {
        const thread = await api.vaultMailThread(tid);
        editingPath = path;
        currentRawContent = '';

        const title = thread.root_subject || doc?.title || path.split('/').pop().replace('.md', '');
        if (detailTitle) detailTitle.textContent = title;

        // Tags from participants / thread
        renderTagPills(doc?.tags || []);

        // Meta line — thread summary
        if (detailMetaLine) {
          const count = thread.thread_message_count || 0;
          const cLabel = count < 2 ? 'письмо' : count < 5 ? 'письма' : 'писем';
          const parts = (thread.participants || []).join(', ');
          detailMetaLine.innerHTML = `<span class="meta-sender">📧 ${count} ${cLabel}</span>` +
            (parts ? `<span class="meta-sep">·</span><span>${escHtml(parts)}</span>` : '');
        }

        if (detailContent) {
          detailContent.innerHTML = '';
          (thread.items || []).forEach(item => {
            const wrap = document.createElement('div');
            wrap.style.cssText = 'margin-bottom:14px;padding-bottom:12px;border-bottom:1px solid var(--color-border)';
            const hdr = document.createElement('div');
            hdr.style.cssText = 'font-size:12px;color:var(--color-text-muted);margin-bottom:4px;display:flex;gap:8px';
            hdr.innerHTML = `<span>${escHtml(item.sender || '')}</span><span style="margin-left:auto">${escHtml((item.date_iso || '').slice(0,16).replace('T',' '))}</span>`;
            const body = document.createElement('div');
            body.style.cssText = 'white-space:pre-wrap;font-size:13px;line-height:1.6';
            body.textContent = item.full_body || '';
            wrap.appendChild(hdr);
            wrap.appendChild(body);
            if (item.attachments?.length) {
              const att = document.createElement('div');
              att.style.cssText = 'font-size:11px;color:var(--color-text-muted);margin-top:4px';
              att.textContent = '📎 ' + item.attachments.map(a => a.filename).join(', ');
              wrap.appendChild(att);
            }
            detailContent.appendChild(wrap);
          });
        }

        renderToolBar(tid, section, title, true, thread.last_message_id || null);
        return;
      }

      // Load raw file
      const data = await api.vaultFile(path);
      editingPath = path;
      currentRawContent = data.content || '';

      const fm = parseFrontmatter(currentRawContent);
      const title = fm.title || doc?.title || path.split('/').pop().replace('.md', '');
      if (detailTitle) detailTitle.textContent = title;

      // Tags from frontmatter
      const fmTags = fm.tags
        ? fm.tags.replace(/^\[|\]$/g, '').split(',').map(t => t.trim().replace(/^["']|["']$/g, '')).filter(Boolean)
        : (doc?.tags || []);
      renderTagPills(fmTags);

      // Meta line
      renderMetaLine(fm, section);

      // Body
      const bodyStart = currentRawContent.startsWith('---')
        ? (currentRawContent.indexOf('\n---', 3) + 4) : 0;
      if (detailContent) detailContent.textContent = currentRawContent.slice(bodyStart).trimStart();

      const singleMsgId = (section === 'mail') ? (fm.message_id || '') : '';
      renderToolBar(path, section, title, false, singleMsgId);

    } catch (err) {
      showToast('Ошибка открытия файла: ' + err.message, 'error');
    }
  }

  function closeDetail() {
    if (detailPanel) detailPanel.style.display = 'none';
    if (viewerEmpty) viewerEmpty.style.display = '';
    currentOpenPath = null;
    editingPath = null;
    updateBreadcrumb(null);
    showMentionedEmpty();
  }

  detailClose?.addEventListener('click', closeDetail);

  detailEdit?.addEventListener('click', () => {
    if (!editingPath) return;
    const ta = document.createElement('textarea');
    ta.value = currentRawContent;
    ta.className = 'vault__detail-editor';
    ta.style.cssText = 'width:100%;min-height:250px;font-family:monospace;font-size:12px;padding:12px;border:1px solid var(--color-border);border-radius:8px;resize:vertical;background:var(--color-bg);color:var(--color-text);margin-top:8px';
    if (detailContent) {
      detailContent.innerHTML = '';
      detailContent.appendChild(ta);
    }
    const saveBtn = document.createElement('button');
    saveBtn.className = 'vault__detail-save-btn';
    saveBtn.textContent = 'Сохранить';
    saveBtn.style.marginTop = '8px';
    saveBtn.addEventListener('click', async () => {
      try {
        const savedPath = editingPath;
        await api.vaultSave(savedPath, ta.value);
        showToast('Файл сохранён', 'success');
        const doc = allDocs.find(d => d.path === savedPath) || { section: sectionFromPath(savedPath) };
        openDetail(savedPath, doc);
        loadDocs();
      } catch (err) {
        showToast('Ошибка сохранения: ' + err.message, 'error');
      }
    });
    if (detailContent) detailContent.appendChild(saveBtn);
  });

  detailDelete?.addEventListener('click', async () => {
    if (!editingPath || !confirm('Удалить этот файл?')) return;
    try {
      await api.vaultDelete(editingPath);
      showToast('Файл удалён', 'success');
      closeDetail();
      loadDocs();
    } catch (err) {
      showToast('Ошибка удаления: ' + err.message, 'error');
    }
  });

  // ── Search, sort, reload ──────────────────────────────────────────────────
  searchEl?.addEventListener('input', debounce(() => renderList(), 200));
  sortEl?.addEventListener('change', () => renderList());

  reloadBtn?.addEventListener('click', async () => {
    try {
      reloadBtn.disabled = true;
      await api.vaultReload();
      await loadDocs();
      showToast('Vault перезагружен', 'success');
    } catch (err) {
      showToast('Ошибка перезагрузки: ' + err.message, 'error');
    } finally {
      reloadBtn.disabled = false;
    }
  });

  // ── Cross-tab events ──────────────────────────────────────────────────────
  document.addEventListener('pa:tags-reset', async () => {
    activeTags.clear(); activeUrgency = ''; activeCategory = '';
    await loadTags();
    await loadDocs();
  });

  document.addEventListener('pa:vault-reloaded', async () => {
    await loadTags();
    await loadDocs();
  });

  document.addEventListener('vault:open', (e) => {
    const { path } = e.detail || {};
    if (!path) return;
    const doc = allDocs.find(d => d.path === path) || { section: sectionFromPath(path) };
    openDetail(path, doc);
  });

  // ── Init ──────────────────────────────────────────────────────────────────
  loadDocs();
  loadTags();
}
