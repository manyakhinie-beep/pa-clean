// =============================================================================
// inbox.js — 3-panel inbox: list | detail | assistant
// Design reference: Personal Assistant workspace.pdf
//
// Features:
//   • Server-side read/unread state (via /api/v1/inbox/{id}/read|unread)
//   • Filter: all | urgent | important | mail | calendar
//   • Assistant panel: summary, suggestions, tag picker, project assign, read toggle
//   • Structured extraction: action items, intent badge, entities, reply_required
//   • Draft/summarize actions open chat WITH vault document in context chips
//   • Keyboard shortcuts: J/K nav, R draft, U summarize, E archive, P project, S snooze, C meeting
// =============================================================================
import { api } from './api.js?v=20260520153419';

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------
let _items  = [];
let _stats  = { total: 0, unread: 0, urgent: 0, important: 0, followup: 0 };
let _activeIdx  = -1;
let _filter     = 'all';
let _sortBy     = 'date';    // 'date' | 'priority'
let _search     = '';        // quick search query — filters _items by ФИО/тема/превью
let _groupByThread = true;   // Outlook-style Conversations view (default on)
let _expandedThreads = new Set();  // thread_id values currently expanded
let _ctx        = null;
let _summaryCache    = {};   // item_id → summary string
let _suggestCache    = {};   // item_id → { next_actions, tag_suggestions }
let _extractCache    = {};   // item_id → ExtractionResult dict
let _threadGraphCache = {};  // thread_id → ThreadGraph dict
let _projectsCache   = null; // null | array
let _loading    = false;

// ---------------------------------------------------------------------------
// Intent metadata
// ---------------------------------------------------------------------------
const INTENT_META = {
  request:  { label: 'Запрос',   emoji: '📋', cls: 'intent--request'  },
  question: { label: 'Вопрос',   emoji: '❓', cls: 'intent--question' },
  deadline: { label: 'Дедлайн',  emoji: '⏰', cls: 'intent--deadline' },
  meeting:  { label: 'Встреча',  emoji: '📅', cls: 'intent--meeting'  },
  fyi:      { label: 'FYI',      emoji: 'ℹ️', cls: 'intent--fyi'      },
  info:     { label: 'Инфо',     emoji: 'ℹ️', cls: 'intent--fyi'      },
  unknown:  { label: '',         emoji: '',   cls: ''                  },
};

// ---------------------------------------------------------------------------
// DOM helpers
// ---------------------------------------------------------------------------
const $ = id => document.getElementById(id);

function _esc(s) {
  return String(s || '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

// ---------------------------------------------------------------------------
// Tag rendering — uses shared rules-tag-pill CSS classes
// ---------------------------------------------------------------------------
function renderTagBadges(tags) {
  if (!Array.isArray(tags) || !tags.length) return '';
  return tags.map(t => {
    const cls   = t.cls || 'default';
    const label = t.label || cls;
    return `<span class="rules-tag-pill rules-tag-pill--${_esc(cls)}">${_esc(label)}</span>`;
  }).join('');
}

// ---------------------------------------------------------------------------
// Quick search — client-side filter across sender ФИО / email / subject /
// preview / tags / sender_role.  Whitespace splits the query into AND-tokens
// so "иванов смета" matches items containing both substrings (case- and
// diacritic-insensitive).
// ---------------------------------------------------------------------------
function _normalize(s) {
  if (s == null) return '';
  // NFD strips combining marks (U+0300..U+036F) so accented variants and
  // "ё"/"е" all match. Lowercase for case-insensitive compare.
  return String(s).normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();
}

function _itemMatchesSearch(item, tokens) {
  if (!tokens.length) return true;
  const tagLabels = Array.isArray(item.tags)
    ? item.tags.map(t => t.label || t.cls || (typeof t === 'string' ? t : '')).join(' ')
    : '';
  const haystack = _normalize([
    item.sender_name,
    item.sender_email,
    item.sender_role,
    item.subject,
    item.body_preview || item.preview,
    tagLabels,
  ].filter(Boolean).join(' \n '));
  return tokens.every(tok => haystack.includes(tok));
}

function _filteredItems() {
  if (!_search.trim()) return _items;
  const tokens = _normalize(_search).split(/\s+/).filter(Boolean);
  if (!tokens.length) return _items;
  return _items.filter(it => _itemMatchesSearch(it, tokens));
}

// ---------------------------------------------------------------------------
// List rendering
// ---------------------------------------------------------------------------
function renderList() {
  const container = $('ib-list');
  if (!container) return;

  if (_loading) {
    container.innerHTML = '<div class="ib-list-empty"><div class="ib-spinner"></div>Загрузка…</div>';
    return;
  }
  if (!_items.length) {
    container.innerHTML = `
      <div class="ib-list-empty">
        <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.2">
          <path stroke-linecap="round" stroke-linejoin="round" d="M21.75 6.75v10.5a2.25 2.25 0 01-2.25 2.25h-15a2.25 2.25 0 01-2.25-2.25V6.75m19.5 0A2.25 2.25 0 0019.5 4.5h-15a2.25 2.25 0 00-2.25 2.25m19.5 0v.243a2.25 2.25 0 01-1.07 1.916l-7.5 4.615a2.25 2.25 0 01-2.36 0L3.32 8.91a2.25 2.25 0 01-1.07-1.916V6.75"/>
        </svg>
        <span>Inbox пуст</span>
        <small>Запустите синхронизацию в Настройках</small>
      </div>`;
    return;
  }

  // Apply quick-search filter; preserve original _items indices so the
  // existing keyboard/click handlers and _activeIdx semantics keep working.
  const visible = _filteredItems();
  if (!visible.length && _search.trim()) {
    container.innerHTML = `
      <div class="ib-list-empty">
        <span>Ничего не найдено</span>
        <small>Запрос «${_esc(_search.trim())}» — попробуйте другие слова или Esc</small>
      </div>`;
    return;
  }

  // Flat OR grouped rendering depending on _groupByThread.
  if (_groupByThread) {
    container.innerHTML = _renderThreadGroups(visible);
    _wireThreadGroupHandlers(container);
  } else {
    // Flat view — bucket by time (Сегодня / Вчера / На этой неделе / Раньше)
    // so the reader has the same mental anchor Apple Mail and Superhuman use.
    container.innerHTML = _renderTimeBuckets(visible);
    container.querySelectorAll('.ib-item').forEach(el => {
      el.addEventListener('click', () => {
        const idx = parseInt(el.dataset.idx, 10);
        selectItem(idx);
      });
    });
  }
}

// ---------------------------------------------------------------------------
// Time-bucket grouping for the flat view
// ---------------------------------------------------------------------------
function _timeBucket(dateStr) {
  if (!dateStr) return 'older';
  const d = new Date(dateStr);
  if (Number.isNaN(d.getTime())) return 'older';
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const dDate = new Date(d.getFullYear(), d.getMonth(), d.getDate());
  const diffDays = Math.floor((today - dDate) / 86_400_000);
  if (diffDays <= 0)  return 'today';
  if (diffDays === 1) return 'yesterday';
  if (diffDays <= 7)  return 'week';
  if (diffDays <= 30) return 'month';
  return 'older';
}

const _BUCKET_LABELS = {
  today:     'Сегодня',
  yesterday: 'Вчера',
  week:      'На этой неделе',
  month:     'Ранее в этом месяце',
  older:     'Старше',
};
const _BUCKET_ORDER = ['today', 'yesterday', 'week', 'month', 'older'];

function _renderTimeBuckets(visible) {
  const buckets = new Map();
  visible.forEach(item => {
    const key = _timeBucket(item.date);
    if (!buckets.has(key)) buckets.set(key, []);
    buckets.get(key).push(item);
  });
  return _BUCKET_ORDER
    .filter(b => buckets.has(b))
    .map(b => {
      const list = buckets.get(b);
      return `<div class="ib-bucket-header">
                <span class="ib-bucket-header__label">${_BUCKET_LABELS[b]}</span>
                <span class="ib-bucket-header__count">${list.length}</span>
              </div>` +
             list.map(item => _renderItemHtml(item, false)).join('');
    })
    .join('');
}

/** Render a single inbox row.  Used for both flat lists and inside expanded
 *  thread groups (with ``inThread=true`` for slight indentation). */
function _renderItemHtml(item, inThread = false) {
  const i = _items.indexOf(item);
  const active  = i === _activeIdx ? ' ib-item--active'  : '';
  const read    = item.read        ? ' ib-item--read'    : '';
  const urgent  = item.is_urgent   ? ' ib-item--urgent'  : '';
  const indent  = inThread         ? ' ib-item--in-thread' : '';
  const tags    = renderTagBadges(item.tags);
  const typeIcon = item.type === 'meeting'
    ? '<svg class="ib-item-type-icon" viewBox="0 0 20 20" fill="currentColor"><path d="M6 2a1 1 0 00-1 1v1H4a2 2 0 00-2 2v10a2 2 0 002 2h12a2 2 0 002-2V6a2 2 0 00-2-2h-1V3a1 1 0 10-2 0v1H7V3a1 1 0 00-1-1zm0 5a1 1 0 000 2h8a1 1 0 100-2H6z"/></svg>'
    : '';
  const unreadDot = !item.read
    ? '<span class="ib-unread-dot" aria-label="Непрочитано"></span>'
    : '';

  // Extraction badges from cached data
  const extr = item.extraction || _extractCache[item.id];
  const intentMeta = extr ? (INTENT_META[extr.intent] || INTENT_META.unknown) : null;
  const intentBadge = intentMeta && intentMeta.emoji
    ? `<span class="ib-intent-badge ${_esc(intentMeta.cls)}" title="${_esc(intentMeta.label)}">${intentMeta.emoji}</span>`
    : '';
  const replyBadge = extr?.reply_required
    ? '<span class="ib-reply-badge" title="Ожидает ответа">💬</span>'
    : '';
  const actionsBadge = extr?.action_items?.length
    ? `<span class="ib-actions-badge" title="${extr.action_items.length} задач">📋${extr.action_items.length}</span>`
    : '';

  // Priority bar — only when priority > 0
  const prio = item.priority || 0;
  const prioLabel = item.priority_label || 'low';
  const prioBar = prio > 0
    ? `<div class="ib-priority-bar ib-priority-bar--${_esc(prioLabel)}" style="--prio:${prio}" title="Приоритет: ${prio}/100" aria-label="Приоритет ${prio}"></div>`
    : '<div class="ib-priority-bar ib-priority-bar--none"></div>';

  // Follow-up bell
  const followupBadge = item.followup_needed
    ? '<span class="ib-followup-badge" title="Ожидает вашего ответа">🔔</span>'
    : '';

  // Stage 8: AI badge — shown when item has 'ai_classified' tag
  const isAIClassified = Array.isArray(item.tags) && item.tags.some(t => {
    const label = t.label || t.cls || String(t);
    return label === 'ai_classified' || (t.cls && t.cls === 'ai_classified');
  });
  const aiBadge = isAIClassified
    ? '<span class="ib-ai-badge" title="Классифицировано ИИ">🤖</span>'
    : '';

  // Two-line snippet of the email body — gives reading-list comfort like
  // Apple Mail / Superhuman, so the user can skim without opening each item.
  // Skip for meetings (their preview is usually noisy markdown table).
  const previewText = item.type === 'meeting'
    ? ''
    : (item.body_preview || item.preview || '').trim();
  const previewHtml = previewText
    ? `<div class="ib-item-preview">${_esc(previewText.slice(0, 200))}</div>`
    : '';

  return `
  <div class="ib-item${active}${read}${urgent}${indent}" data-idx="${i}" data-id="${_esc(item.id)}">
    ${prioBar}
    <label class="ib-item-check" onclick="event.stopPropagation()">
      <input type="checkbox" class="ib-checkbox" data-id="${_esc(item.id)}">
      <span class="ib-checkbox-ui"></span>
    </label>
    <div class="ib-avatar" style="background:${item.sender_color}">${_esc(item.sender_initials)}</div>
    <div class="ib-item-body">
      <div class="ib-item-top">
        ${unreadDot}
        <span class="ib-item-sender">${_esc(item.sender_name)}</span>
        ${item.sender_role ? `<span class="ib-item-role">${_esc(item.sender_role)}</span>` : ''}
        <span class="ib-item-badges">${intentBadge}${replyBadge}${actionsBadge}${followupBadge}${aiBadge}</span>
        <span class="ib-item-time">${_esc(item.time_label)}</span>
      </div>
      <div class="ib-item-subject">${typeIcon}${_esc(item.subject)}</div>
      ${previewHtml}
      ${tags ? `<div class="ib-item-tags">${tags}</div>` : ''}
    </div>
  </div>`;
}

// ---------------------------------------------------------------------------
// Thread grouping ("Беседы" view, like Outlook Conversations)
// ---------------------------------------------------------------------------

/** Strip "Re: " / "Fwd: " / "Отв: " / "Пер: " prefixes for thread subject. */
function _cleanSubject(s) {
  return String(s || '').replace(/^\s*(?:re|fwd?|отв|пер|aw|tr|sv)\s*(?:\[\d+\])?\s*:\s*/i, '').trim();
}

/** Group visible items by ``thread_id``.  Items without a thread_id are
 *  treated as their own single-message threads (key = item.id). Returns
 *  groups in latest-activity-first order; each group has its messages
 *  sorted newest-first. */
function _groupVisibleByThread(visible) {
  const groups = new Map();
  visible.forEach(item => {
    const key = (item.thread_id && String(item.thread_id).trim()) || `__single__:${item.id}`;
    if (!groups.has(key)) {
      groups.set(key, { thread_id: key, messages: [] });
    }
    groups.get(key).messages.push(item);
  });

  const result = [];
  groups.forEach(g => {
    // Newest first inside the group — matches Mail.app and Outlook default.
    g.messages.sort((a, b) => String(b.date || '').localeCompare(String(a.date || '')));
    const latest = g.messages[0];
    g.subject = _cleanSubject(latest.subject || '');
    g.latest = latest;
    g.unread_count = g.messages.filter(m => !m.read).length;
    g.urgent_count = g.messages.filter(m => m.is_urgent).length;
    g.followup_count = g.messages.filter(m => m.followup_needed).length;
    // Deduplicate sender list — keep order of first appearance
    const seen = new Set();
    g.senders = [];
    g.messages.forEach(m => {
      const name = m.sender_name || m.sender_email || '?';
      if (!seen.has(name)) {
        seen.add(name);
        g.senders.push(name);
      }
    });
    result.push(g);
  });

  // Threads with most recent activity first.
  result.sort((a, b) => String(b.latest.date || '').localeCompare(String(a.latest.date || '')));
  return result;
}

/** HTML for a thread group header + expanded children when applicable. */
function _renderThreadGroups(visible) {
  const groups = _groupVisibleByThread(visible);
  if (!groups.length) return '';

  return groups.map(g => {
    const isSingle = g.messages.length === 1;
    const expanded = isSingle || _expandedThreads.has(g.thread_id);
    // For single-message threads, just render the row directly (no chevron).
    if (isSingle) {
      return _renderItemHtml(g.latest, false);
    }

    const latest = g.latest;
    const i = _items.indexOf(latest);
    const sendersText = g.senders.slice(0, 3).join(', ') +
      (g.senders.length > 3 ? ` +${g.senders.length - 3}` : '');
    const unreadBadge = g.unread_count
      ? `<span class="ib-thread-unread" title="${g.unread_count} непрочитанных">${g.unread_count}</span>`
      : '';
    const urgentChip = g.urgent_count
      ? '<span class="ib-thread-urgent" title="Срочно в треде">🔴</span>'
      : '';
    const followupChip = g.followup_count
      ? '<span class="ib-thread-followup" title="Ожидает ответа">🔔</span>'
      : '';
    const chevron = expanded ? '▾' : '▸';

    const childrenHtml = expanded
      ? `<div class="ib-thread-children">${
          g.messages.map(m => _renderItemHtml(m, true)).join('')
        }</div>`
      : '';

    return `
    <div class="ib-thread${expanded ? ' ib-thread--expanded' : ''}"
         data-thread-id="${_esc(g.thread_id)}"
         data-latest-idx="${i}">
      <div class="ib-thread-header" role="button" tabindex="0">
        <span class="ib-thread-chevron">${chevron}</span>
        <div class="ib-avatar ib-thread-avatar" style="background:${latest.sender_color}">${_esc(latest.sender_initials)}</div>
        <div class="ib-thread-body">
          <div class="ib-thread-top">
            ${g.unread_count ? '<span class="ib-unread-dot"></span>' : ''}
            <span class="ib-thread-senders">${_esc(sendersText)}</span>
            <span class="ib-thread-badges">${urgentChip}${followupChip}${unreadBadge}</span>
            <span class="ib-item-time">${_esc(latest.time_label)}</span>
          </div>
          <div class="ib-thread-subject">
            <span class="ib-thread-count">[${g.messages.length}]</span>
            ${_esc(g.subject || '(без темы)')}
          </div>
        </div>
      </div>
      ${childrenHtml}
    </div>`;
  }).join('');
}

/** Wire click handlers for thread headers + child items. */
function _wireThreadGroupHandlers(container) {
  container.querySelectorAll('.ib-thread-header').forEach(el => {
    const parent = el.closest('.ib-thread');
    if (!parent) return;
    const tid = parent.dataset.threadId;
    const onToggle = () => {
      if (_expandedThreads.has(tid)) _expandedThreads.delete(tid);
      else _expandedThreads.add(tid);
      renderList();
    };
    el.addEventListener('click', onToggle);
    el.addEventListener('keydown', e => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onToggle(); }
    });
  });
  // Child item clicks open the message detail.
  container.querySelectorAll('.ib-thread .ib-item').forEach(el => {
    el.addEventListener('click', () => {
      const idx = parseInt(el.dataset.idx, 10);
      selectItem(idx);
    });
  });
  // Single-message "threads" rendered flat — same click as flat list.
  container.querySelectorAll(':scope > .ib-item').forEach(el => {
    el.addEventListener('click', () => {
      const idx = parseInt(el.dataset.idx, 10);
      selectItem(idx);
    });
  });
}

// ---------------------------------------------------------------------------
// Detail panel
// ---------------------------------------------------------------------------
function renderDetail() {
  const panel = $('ib-detail');
  if (!panel) return;

  if (_activeIdx < 0 || _activeIdx >= _items.length) {
    panel.innerHTML = `
      <div class="ib-detail-empty">
        <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.2">
          <path stroke-linecap="round" stroke-linejoin="round" d="M21.75 6.75v10.5a2.25 2.25 0 01-2.25 2.25h-15a2.25 2.25 0 01-2.25-2.25V6.75m19.5 0A2.25 2.25 0 0019.5 4.5h-15a2.25 2.25 0 00-2.25 2.25m19.5 0v.243a2.25 2.25 0 01-1.07 1.916l-7.5 4.615a2.25 2.25 0 01-2.36 0L3.32 8.91a2.25 2.25 0 01-1.07-1.916V6.75"/>
        </svg>
        <span>Выберите письмо</span>
      </div>`;
    return;
  }

  const item  = _items[_activeIdx];
  const tags  = renderTagBadges(item.tags);
  const bodyHtml  = _md2html(item.body || item.body_preview || '');
  const threadCount = item.thread_count || 0;
  const threadNote  = threadCount > 1 ? `· тред из ${threadCount} писем` : '';

  // Thread graph placeholder (rendered async after paint)
  const graphPlaceholder = (threadCount > 1 && item.thread_id)
    ? `<div class="ib-thread-graph" id="ib-thread-graph-${_esc(item.id)}" data-thread-id="${_esc(item.thread_id)}">
         <div class="ib-thread-graph__loading">
           <span class="ib-thread-graph__spinner"></span> Загрузка участников…
         </div>
       </div>`
    : '';

  panel.innerHTML = `
    <div class="ib-detail-header">
      ${tags ? `<div class="ib-detail-tags">${tags}</div>` : ''}
      <h2 class="ib-detail-subject">${_esc(item.subject)}</h2>
      <div class="ib-detail-meta">
        <div class="ib-avatar ib-avatar--lg" style="background:${item.sender_color}">${_esc(item.sender_initials)}</div>
        <div class="ib-detail-meta-info">
          <span class="ib-detail-sender">${_esc(item.sender_name)}</span>
          ${item.sender_role ? `<span class="ib-detail-role">· ${_esc(item.sender_role)}</span>` : ''}
          <span class="ib-detail-time">· ${_esc(item.time_label)} ${threadNote}</span>
        </div>
      </div>
    </div>
    <div class="ib-detail-body" id="ib-detail-body">${bodyHtml}</div>
    ${graphPlaceholder}`;

  // Load graph async — don't block initial render
  if (threadCount > 1 && item.thread_id) {
    loadThreadGraph(item.id, item.thread_id);
  }
}

// ---------------------------------------------------------------------------
// Thread Participant Graph
// ---------------------------------------------------------------------------

async function loadThreadGraph(itemId, threadId) {
  const placeholder = document.getElementById(`ib-thread-graph-${itemId}`);
  if (!placeholder) return;

  // Use cache if available
  if (_threadGraphCache[threadId]) {
    renderThreadGraph(placeholder, _threadGraphCache[threadId]);
    return;
  }

  try {
    const graph = await api.inboxThreadGraph(threadId);
    _threadGraphCache[threadId] = graph;
    renderThreadGraph(placeholder, graph);
  } catch (err) {
    // Gracefully hide on error — thread graph is progressive enhancement
    if (placeholder) placeholder.remove();
  }
}

function renderThreadGraph(el, g) {
  if (!el || !g) return;

  // ── My-turn banner ────────────────────────────────────────────────────
  const myTurnBanner = g.my_turn
    ? `<div class="ib-thread-graph__my-turn">
         <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
           <path stroke-linecap="round" stroke-linejoin="round" d="M12 9v3.75m9-.75a9 9 0 11-18 0 9 9 0 0118 0zm-9 3.75h.008v.008H12v-.008z"/>
         </svg>
         Ждёт вашего ответа${g.days_without_reply > 0 ? ` · ${g.days_without_reply} ${_dayWord(g.days_without_reply)}` : ''}
       </div>`
    : '';

  // ── Participants row ──────────────────────────────────────────────────
  const MAX_VISIBLE = 5;
  const visible = g.participants.slice(0, MAX_VISIBLE);
  const extra   = g.participants.length - MAX_VISIBLE;

  const avatarHtml = visible.map(p => {
    const tip  = `${_esc(p.name)}${p.role === 'initiator' ? ' · инициатор' : ''}${p.role === 'observer' ? ' · наблюдатель' : ''}`;
    const ring = p.is_me ? ' ib-thread-graph__avatar--me' : '';
    const sent = p.messages_sent > 0
      ? `<span class="ib-thread-graph__avatar-count">${p.messages_sent}</span>`
      : '';
    return `<div class="ib-thread-graph__avatar${ring}" style="background:${_esc(p.avatar_color)}" title="${tip}">
              ${_esc(p.initials)}${sent}
            </div>`;
  }).join('');

  const extraHtml = extra > 0
    ? `<div class="ib-thread-graph__avatar ib-thread-graph__avatar--extra">+${extra}</div>`
    : '';

  // ── Timeline ──────────────────────────────────────────────────────────
  const MAX_TIMELINE = 6;
  const timelineEntries = g.timeline.slice(0, MAX_TIMELINE);
  const timelineHtml = timelineEntries.map((t, i) => {
    const isLast  = i === g.timeline.length - 1;
    const meClass = t.is_me ? ' ib-thread-graph__tl-entry--me' : '';
    const lastClass = isLast ? ' ib-thread-graph__tl-entry--last' : '';
    return `<div class="ib-thread-graph__tl-entry${meClass}${lastClass}" data-path="${_esc(t.path)}">
              <div class="ib-thread-graph__tl-dot"></div>
              <div class="ib-thread-graph__tl-content">
                <span class="ib-thread-graph__tl-sender">${_esc(t.sender_name)}${t.is_me ? ' (я)' : ''}</span>
                <span class="ib-thread-graph__tl-date">${_esc(t.date_display)}</span>
              </div>
            </div>`;
  }).join('');

  const moreTimeline = g.timeline.length > MAX_TIMELINE
    ? `<div class="ib-thread-graph__tl-more">ещё ${g.timeline.length - MAX_TIMELINE} сообщ.</div>`
    : '';

  el.innerHTML = `
    <div class="ib-thread-graph__header">
      <span class="ib-thread-graph__title">Участники треда</span>
      <span class="ib-thread-graph__count">${g.participant_count} чел. · ${g.message_count} писем</span>
    </div>
    ${myTurnBanner}
    <div class="ib-thread-graph__avatars">${avatarHtml}${extraHtml}</div>
    <div class="ib-thread-graph__timeline">${timelineHtml}${moreTimeline}</div>`;

  // Click on timeline entry → open that vault doc
  el.querySelectorAll('.ib-thread-graph__tl-entry[data-path]').forEach(row => {
    row.style.cursor = 'pointer';
    row.addEventListener('click', () => {
      const path = row.dataset.path;
      if (path) document.dispatchEvent(new CustomEvent('vault:open', { detail: { path } }));
    });
  });
}

function _dayWord(n) {
  if (n % 10 === 1 && n % 100 !== 11) return 'день';
  if ([2,3,4].includes(n % 10) && ![12,13,14].includes(n % 100)) return 'дня';
  return 'дней';
}

// ---------------------------------------------------------------------------
// Assistant panel
// ---------------------------------------------------------------------------
function renderAssistant() {
  const panel = $('ib-assistant');
  if (!panel) return;

  if (_activeIdx < 0 || _activeIdx >= _items.length) {
    panel.innerHTML = `
      <div class="ib-assistant-header">
        <div class="ib-assistant-avatar">PA</div>
        <div class="ib-assistant-info">
          <span class="ib-assistant-name">Ассистент</span>
          <span class="ib-badge ib-badge--ready">готов</span>
        </div>
      </div>
      <div class="ib-assistant-idle">Выберите письмо для анализа</div>`;
    return;
  }

  const item    = _items[_activeIdx];
  const summary = _summaryCache[item.id] || null;
  const suggests = _suggestCache[item.id] || null;
  const extr    = item.extraction || _extractCache[item.id];

  const readLabel  = item.read ? 'Пометить непрочитанным' : 'Пометить прочитанным';
  const readAction = item.read ? 'unread' : 'read';
  const readIcon   = item.read
    ? `<svg class="ib-action-icon" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5"><path stroke-linecap="round" stroke-linejoin="round" d="M3 19l9-7 9 7M3 6l9 7 9-7"/></svg>`
    : `<svg class="ib-action-icon" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5"><path stroke-linecap="round" stroke-linejoin="round" d="M2.25 6.75c0 8.284 6.716 15 15 15h2.25a2.25 2.25 0 002.25-2.25v-1.372c0-.516-.351-.966-.852-1.091l-4.423-1.106c-.44-.11-.902.055-1.173.417l-.97 1.293c-.282.376-.769.542-1.21.38a12.035 12.035 0 01-7.143-7.143c-.162-.441.004-.928.38-1.21l1.293-.97c.363-.271.527-.734.417-1.173L6.963 3.102a1.125 1.125 0 00-1.091-.852H4.5A2.25 2.25 0 002.25 4.5v2.25z"/></svg>`;

  // Suggestions rendering
  let suggestHtml = '';
  if (suggests) {
    const actions = (suggests.next_actions || []).map(a =>
      `<button class="ib-suggest-action" data-suggest="${_esc(a)}">${_esc(a)}</button>`
    ).join('');
    const tagHints = (suggests.tag_suggestions || []).map(t =>
      `<button class="ib-tag-hint" data-tag="${_esc(t)}">${_esc(t)}</button>`
    ).join('');
    suggestHtml = `
    <div class="ib-section">
      <div class="ib-section-label">ПРЕДЛОЖЕНИЯ АССИСТЕНТА</div>
      ${actions ? `<div class="ib-suggest-actions">${actions}</div>` : ''}
      ${tagHints ? `<div class="ib-tag-hints">
        <span class="ib-tag-hints-label">Теги:</span>${tagHints}
      </div>` : ''}
    </div>`;
  }

  // Extraction section HTML
  let extrHtml = '';
  if (extr) {
    const intentMeta = INTENT_META[extr.intent] || INTENT_META.unknown;
    const intentRow = intentMeta.emoji
      ? `<div class="ib-extr-intent">
          <span class="ib-intent-badge ${_esc(intentMeta.cls)}">${intentMeta.emoji} ${_esc(intentMeta.label)}</span>
          ${extr.reply_required ? '<span class="ib-extr-reply">💬 нужен ответ</span>' : ''}
          ${extr.tone && extr.tone !== 'neutral'
            ? `<span class="ib-extr-tone ib-extr-tone--${_esc(extr.tone)}">${_esc(extr.tone)}</span>`
            : ''}
        </div>`
      : '';
    const deadlineRow = extr.deadline
      ? `<div class="ib-extr-deadline">⏰ Дедлайн: <strong>${_esc(extr.deadline)}</strong></div>`
      : '';
    const actionRows = (extr.action_items || []).length
      ? `<div class="ib-extr-action-items">${
          extr.action_items.map(a => `
            <div class="ib-action-item">
              <span class="ib-action-item-check">☐</span>
              <span class="ib-action-item-text">${_esc(a.text || a)}</span>
              ${a.deadline ? `<span class="ib-action-item-dl">${_esc(a.deadline)}</span>` : ''}
            </div>`).join('')
        }</div>`
      : '';
    const entities = extr.entities || {};
    const entityGroups = [
      { label: 'Люди',  items: entities.people        || [] },
      { label: 'Орг',   items: entities.organizations || [] },
      { label: 'Суммы', items: entities.amounts        || [] },
      { label: 'Даты',  items: entities.dates          || [] },
    ].filter(g => g.items.length > 0);
    const entitiesHtml = entityGroups.length
      ? `<div class="ib-extr-entities">${
          entityGroups.map(g => `
            <div class="ib-entity-group">
              <span class="ib-entity-label">${_esc(g.label)}</span>
              <span class="ib-entity-chips">${
                g.items.map(e => `<span class="ib-entity-chip">${_esc(e)}</span>`).join('')
              }</span>
            </div>`).join('')
        }</div>`
      : '';
    extrHtml = intentRow + deadlineRow + actionRows + entitiesHtml;
  } else if (_extractCache[item.id] === undefined) {
    // Not yet requested → show loading state
    extrHtml = '<span class="ib-summary-loading">Анализируем…</span>';
  } else {
    // null → extraction failed gracefully
    extrHtml = '<span class="ib-summary-loading">Анализ недоступен</span>';
  }

  // Project badge
  const projectBadge = item.project_id
    ? `<span class="ib-project-badge">📁 ${_esc(item.project_name || item.project_id)}</span>`
    : '';

  panel.innerHTML = `
    <div class="ib-assistant-header">
      <div class="ib-assistant-avatar">PA</div>
      <div class="ib-assistant-info">
        <span class="ib-assistant-name">Ассистент</span>
        <span class="ib-badge ib-badge--ready">готов</span>
      </div>
    </div>

    <div class="ib-section">
      <div class="ib-section-label">СВОДКА</div>
      <div class="ib-summary" id="ib-summary">
        ${summary
          ? _esc(summary)
          : '<span class="ib-summary-loading">Генерация…</span>'}
      </div>
    </div>

    <div class="ib-section" id="ib-extraction-section">
      <div class="ib-section-label">АНАЛИЗ</div>
      <div class="ib-extraction-body">${extrHtml}</div>
    </div>

    <div class="ib-section">
      <div class="ib-section-label">ДЕЙСТВИЯ</div>
      <div class="ib-actions">
        <button class="ib-action" data-action="draft" title="R">
          <svg class="ib-action-icon" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5"><path stroke-linecap="round" stroke-linejoin="round" d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z"/></svg>
          <span>Draft ответа</span>
          <kbd>R</kbd>
        </button>
        <button class="ib-action" data-action="delegate" title="D">
          <svg class="ib-action-icon" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5"><path stroke-linecap="round" stroke-linejoin="round" d="M17 20h5v-2a4 4 0 00-3-3.87M9 20H4v-2a4 4 0 014-4h.5M16 3.13a4 4 0 010 7.75M13 7a4 4 0 11-8 0 4 4 0 018 0z"/></svg>
          <span>Делегировать</span>
          <kbd>D</kbd>
        </button>
        <button class="ib-action" data-action="summarize" title="U">
          <svg class="ib-action-icon" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5"><path stroke-linecap="round" stroke-linejoin="round" d="M4 6h16M4 10h16M4 14h8"/></svg>
          <span>Сводка треда</span>
          <kbd>U</kbd>
        </button>
        <button class="ib-action" data-action="create-meeting" title="C">
          <svg class="ib-action-icon" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5"><path stroke-linecap="round" stroke-linejoin="round" d="M8 7V3m8 4V3m-9 4h10M5 11h14M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z"/></svg>
          <span>Создать слот</span>
          <kbd>C</kbd>
        </button>
        <button class="ib-action" data-action="to-project" title="P">
          <svg class="ib-action-icon" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5"><path stroke-linecap="round" stroke-linejoin="round" d="M3 7a2 2 0 012-2h4l2 2h6a2 2 0 012 2v7a2 2 0 01-2 2H5a2 2 0 01-2-2V7z"/></svg>
          <span>В проект</span>
          <kbd>P</kbd>
        </button>
        <button class="ib-action" data-action="toggle-read" title="${readLabel}">
          ${readIcon}
          <span>${readLabel}</span>
        </button>
        <button class="ib-action" data-action="archive" title="E">
          <svg class="ib-action-icon" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5"><path stroke-linecap="round" stroke-linejoin="round" d="M5 8h14M5 8a2 2 0 110-4h14a2 2 0 110 4M5 8v10a2 2 0 002 2h10a2 2 0 002-2V8M10 12v4"/></svg>
          <span>Архивировать</span>
          <kbd>E</kbd>
        </button>
        <button class="ib-action" data-action="snooze" title="S">
          <svg class="ib-action-icon" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5"><path stroke-linecap="round" stroke-linejoin="round" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>
          <span>Snooze до завтра</span>
          <kbd>S</kbd>
        </button>
      </div>
    </div>

    ${suggestHtml}

    <div class="ib-section">
      <div class="ib-section-label">ТЕГИ</div>
      <div class="ib-tag-editor">
        <div class="ib-tag-current" id="ib-tag-current">
          ${(item.tags_raw || []).map(t => `
            <span class="ib-tag-chip">
              ${_esc(t)}
              <button class="ib-tag-remove" data-tag="${_esc(t)}" title="Удалить тег">×</button>
            </span>`).join('')}
        </div>
        <div class="ib-tag-input-row">
          <input class="ib-tag-input" id="ib-tag-input" type="text" placeholder="Добавить тег…" autocomplete="off">
          <button class="ib-tag-add-btn" id="ib-tag-add-btn">+</button>
        </div>
      </div>
    </div>

    <div class="ib-section">
      <div class="ib-section-label">ПРОЕКТ</div>
      <div class="ib-project-selector">
        ${projectBadge}
        <select class="ib-project-select" id="ib-project-select">
          <option value="">— выберите проект —</option>
        </select>
        <button class="ib-project-assign-btn" id="ib-project-assign-btn">Назначить</button>
      </div>
    </div>

    ${item.thread_id ? `
    <div class="ib-section">
      <div class="ib-section-label">СВЯЗАНО</div>
      <div class="ib-related" id="ib-related">
        <div class="ib-related-item">
          <span class="ib-related-dot ib-related-dot--mail"></span>
          <span>тред · ${_esc(item.thread_id.slice(0, 12))}…</span>
        </div>
      </div>
    </div>` : ''}`;

  // Bind action buttons
  panel.querySelectorAll('.ib-action').forEach(btn => {
    btn.addEventListener('click', () => handleAction(btn.dataset.action));
  });

  // Suggestion action chips
  panel.querySelectorAll('.ib-suggest-action').forEach(btn => {
    btn.addEventListener('click', () => {
      const msg = btn.dataset.suggest;
      _openChat(item, msg);
    });
  });

  // Tag hint chips — add tag on click
  panel.querySelectorAll('.ib-tag-hint').forEach(btn => {
    btn.addEventListener('click', () => applyTag(btn.dataset.tag));
  });

  // Tag remove buttons
  panel.querySelectorAll('.ib-tag-remove').forEach(btn => {
    btn.addEventListener('click', () => removeTag(btn.dataset.tag));
  });

  // Tag input
  const tagInput = $('ib-tag-input');
  const tagAddBtn = $('ib-tag-add-btn');
  if (tagInput) {
    tagInput.addEventListener('keydown', e => {
      if (e.key === 'Enter') { e.preventDefault(); applyTagFromInput(); }
    });
  }
  if (tagAddBtn) {
    tagAddBtn.addEventListener('click', applyTagFromInput);
  }

  // Project select
  loadProjects();

  // Project assign button
  const assignBtn = $('ib-project-assign-btn');
  if (assignBtn) {
    assignBtn.addEventListener('click', assignProject);
  }

  // Auto-load summary, suggestions, extraction if not cached
  if (!_summaryCache[item.id]) loadSummary(item);
  if (!_suggestCache[item.id]) loadSuggestions(item);
  // Extraction is triggered from selectItem(), not here, to avoid double-fetch
}

// ---------------------------------------------------------------------------
// Summary loading
// ---------------------------------------------------------------------------
async function loadSummary(item) {
  try {
    const data = await api.inboxSummarize({ item_id: item.id, body: item.body });
    _summaryCache[item.id] = data.summary || '';
  } catch {
    _summaryCache[item.id] = 'Не удалось получить сводку.';
  }
  const el = $('ib-summary');
  if (el && _items[_activeIdx]?.id === item.id) {
    el.textContent = _summaryCache[item.id];
  }
}

// ---------------------------------------------------------------------------
// Suggestions loading
// ---------------------------------------------------------------------------
async function loadSuggestions(item) {
  try {
    const data = await api.inboxSuggestions(item.id);
    _suggestCache[item.id] = data;
    // Re-render assistant panel if still on same item
    if (_items[_activeIdx]?.id === item.id) renderAssistant();
  } catch {
    _suggestCache[item.id] = { next_actions: [], tag_suggestions: [] };
  }
}

// ---------------------------------------------------------------------------
// Extraction loading (structured analysis via MLX)
// ---------------------------------------------------------------------------
async function loadExtraction(item) {
  try {
    const res = await api.inboxExtract(item.id, item.body || null, false);
    _extractCache[item.id] = res.extraction;
    // Persist onto item object so re-renders pick it up
    const idx = _items.findIndex(it => it.id === item.id);
    if (idx >= 0) _items[idx].extraction = res.extraction;
    if (_items[_activeIdx]?.id === item.id) {
      renderAssistant();
      renderList(); // refresh intent/reply badges in list
    }
  } catch (e) {
    console.warn('[inbox] extraction failed:', e);
    // Cache an empty result to avoid repeated failing requests
    _extractCache[item.id] = null;
  }
}

// ---------------------------------------------------------------------------
// Tag management
// ---------------------------------------------------------------------------
function applyTagFromInput() {
  const input = $('ib-tag-input');
  if (!input) return;
  const tag = input.value.trim();
  if (!tag) return;
  input.value = '';
  applyTag(tag);
}

async function applyTag(tag) {
  const item = _items[_activeIdx];
  if (!item || !tag) return;
  try {
    const res = await api.inboxSetTags(item.id, [tag], 'append');
    const newTags = res.extra_tags || [];
    // Merge into item
    item.tags_raw = [...new Set([...(item.tags_raw || []), ...newTags])];
    renderAssistant();
    _ctx?.showToast?.(`Тег «${tag}» добавлен`, 'success');
  } catch (e) {
    _ctx?.showToast?.('Ошибка при добавлении тега', 'error');
  }
}

async function removeTag(tag) {
  const item = _items[_activeIdx];
  if (!item || !tag) return;
  try {
    const remaining = (item.tags_raw || []).filter(t => t !== tag);
    const res = await api.inboxSetTags(item.id, remaining, 'set');
    item.tags_raw = res.extra_tags !== undefined ? res.extra_tags : remaining;
    renderAssistant();
    _ctx?.showToast?.(`Тег «${tag}» удалён`, 'success');
  } catch {
    _ctx?.showToast?.('Ошибка при удалении тега', 'error');
  }
}

// ---------------------------------------------------------------------------
// Project management
// ---------------------------------------------------------------------------
async function loadProjects() {
  if (_projectsCache === null) {
    try {
      const res = await api.projectsList();
      _projectsCache = res.projects || res || [];
    } catch {
      _projectsCache = [];
    }
  }
  const sel = $('ib-project-select');
  if (!sel) return;
  const item = _items[_activeIdx];
  sel.innerHTML = '<option value="">— выберите проект —</option>' +
    (_projectsCache || []).map(p =>
      `<option value="${_esc(p.id)}" ${item?.project_id === p.id ? 'selected' : ''}>${_esc(p.name)}</option>`
    ).join('');
}

async function assignProject() {
  const item = _items[_activeIdx];
  if (!item) return;
  const sel = $('ib-project-select');
  if (!sel || !sel.value) { _ctx?.showToast?.('Выберите проект', 'info'); return; }
  const project_id   = sel.value;
  const project_name = sel.options[sel.selectedIndex]?.text || '';
  try {
    await api.inboxAssignProject(item.id, project_id, project_name);
    item.project_id   = project_id;
    item.project_name = project_name;
    renderAssistant();
    _ctx?.showToast?.(`Назначен проект «${project_name}»`, 'success');
  } catch {
    _ctx?.showToast?.('Ошибка назначения проекта', 'error');
  }
}

// ---------------------------------------------------------------------------
// Open chat with document context
// ---------------------------------------------------------------------------
function _openChat(item, message, threadCtx = null) {
  // GAP-4 fix: warn when vault path is absent so user knows context is limited.
  // The backend will still receive reply_message_id and attempt its own fallback,
  // but surfacing the warning in the UI helps the user understand the situation.
  if (!item.path) {
    _ctx?.showToast?.(
      'Vault не синхронизирован — откройте Настройки → Синхронизация. Чат откроется без контекста документа.',
      'warning',
    );
  }
  // E-1 fix: dispatch on document (not window) so chat.js document.addEventListener fires.
  // All other modules (vault.js, today.js) already use document — this was the only outlier.
  document.dispatchEvent(new CustomEvent('pa:chat-open', {
    detail: {
      message,
      path: item.path || null,
      vault_thread_id: item.thread_id || null,
      reply_message_id: item.id,
      thread_context: threadCtx,   // Stage 4: full thread context for chat chip
    },
  }));
  _ctx?.activateTab?.('chat');
}

// ---------------------------------------------------------------------------
// Stage 4: Thread-Aware Draft helper
// ---------------------------------------------------------------------------

/**
 * Fetch draft context from backend, then open chat with the enriched prompt.
 * Shows a loading toast while the context is being fetched.
 * Falls back to bare-subject prompt on any error.
 */
async function _openDraftWithContext(item) {
  // Optimistic: show loading indicator
  const draftBtn = document.querySelector('.ib-action[data-action="draft"]');
  const originalLabel = draftBtn ? draftBtn.textContent : null;
  if (draftBtn) {
    draftBtn.textContent = '⏳';
    draftBtn.disabled = true;
  }
  _ctx?.showToast?.('Загружаю контекст треда…', 'info');

  try {
    const ctx = await api.inboxDraftContext(item.id);
    const message = ctx.context_prompt
      || `Составь черновик ответа на письмо от ${item.sender_name}: «${item.subject}»`;
    _openChat(item, message, ctx);
  } catch (err) {
    console.warn('[inbox] draft-context fetch failed, falling back:', err);
    _openChat(
      item,
      `Составь черновик ответа на письмо от ${item.sender_name}: «${item.subject}»`,
    );
  } finally {
    if (draftBtn) {
      draftBtn.textContent = originalLabel || '✏️';
      draftBtn.disabled = false;
    }
  }
}

// ---------------------------------------------------------------------------
// Delegate workflow ("🤝 Делегировать")
// ---------------------------------------------------------------------------
//
// Opens a modal with the colleague list from Rules → Инструменты, lets the
// manager add a short note, then calls /delegate-suggest to get the
// AI-generated intro.  Confirming hands the payload to
// /api/chat/save-draft-mail so Mail.app opens a compose window addressed to
// the colleague.
//
// Falls back to a "configure first" prompt when no colleagues are set.

async function _openDelegatePicker(item) {
  let contacts = [];
  try {
    const res = await api.inboxDelegateContacts();
    contacts = res?.contacts || [];
  } catch (err) {
    _ctx?.showToast?.('Не удалось загрузить список сотрудников: ' + err.message, 'error');
    return;
  }

  if (!contacts.length) {
    _ctx?.showToast?.(
      'Сначала добавьте сотрудников в Правила → Инструменты → Делегирование.',
      'warning',
    );
    _ctx?.activateTab?.('rules');
    return;
  }

  // Remove any existing picker
  document.querySelector('.ib-delegate-modal')?.remove();

  const modal = document.createElement('div');
  modal.className = 'ib-delegate-modal';
  modal.innerHTML = `
    <div class="ib-delegate-modal__backdrop"></div>
    <div class="ib-delegate-modal__panel" role="dialog" aria-modal="true" aria-label="Делегировать письмо">
      <div class="ib-delegate-modal__header">
        <h3>🤝 Делегировать письмо</h3>
        <button class="ib-delegate-modal__close" aria-label="Закрыть">×</button>
      </div>
      <div class="ib-delegate-modal__body">
        <p class="ib-delegate-modal__hint">Кому переслать «${_esc(item.subject || 'без темы')}»?</p>
        <div class="ib-delegate-modal__contacts">
          ${contacts.map((c, idx) => `
            <label class="ib-delegate-contact">
              <input type="radio" name="ib-delegate-target" value="${_esc(c.email)}"
                     ${idx === 0 ? 'checked' : ''}>
              <div class="ib-delegate-contact__body">
                <div class="ib-delegate-contact__name">${_esc(c.name || c.email)}</div>
                <div class="ib-delegate-contact__meta">
                  ${_esc(c.email)}${c.role ? ' · ' + _esc(c.role) : ''}
                </div>
                ${c.note ? `<div class="ib-delegate-contact__note">${_esc(c.note)}</div>` : ''}
              </div>
            </label>`).join('')}
        </div>
        <label class="ib-delegate-modal__note">
          <span>Заметка для коллеги (необязательно)</span>
          <textarea id="ib-delegate-note" rows="2"
                    placeholder="Напр.: «Прошу ускорить, ждут к среде»"></textarea>
        </label>
        <div class="ib-delegate-modal__preview" id="ib-delegate-preview" style="display:none">
          <div class="ib-delegate-modal__preview-label">Письмо коллеге</div>
          <div class="ib-delegate-modal__preview-subject" id="ib-delegate-preview-subject"></div>
          <div class="ib-delegate-modal__preview-intro"   id="ib-delegate-preview-intro"></div>
          <details class="ib-delegate-modal__preview-analysis-wrap" id="ib-delegate-preview-analysis-wrap" style="display:none">
            <summary>Полный анализ (РЕКОМЕНДАЦИЯ / КОНТЕКСТ / ЗАДАЧА / ПРИМЕЧАНИЕ)</summary>
            <div class="ib-delegate-modal__preview-analysis" id="ib-delegate-preview-analysis"></div>
          </details>
          <div class="ib-delegate-modal__preview-flag" id="ib-delegate-preview-flag"></div>
        </div>
      </div>
      <div class="ib-delegate-modal__footer">
        <button class="btn btn--secondary" id="ib-delegate-cancel">Отмена</button>
        <button class="btn btn--secondary" id="ib-delegate-preview-btn">👁 Предпросмотр</button>
        <button class="btn btn--primary"   id="ib-delegate-send">✉️ Открыть в Mail</button>
      </div>
    </div>`;
  document.body.appendChild(modal);

  const close = () => modal.remove();
  modal.querySelector('.ib-delegate-modal__backdrop')?.addEventListener('click', close);
  modal.querySelector('.ib-delegate-modal__close')?.addEventListener('click', close);
  modal.querySelector('#ib-delegate-cancel')?.addEventListener('click', close);

  const _selectedEmail = () =>
    modal.querySelector('input[name="ib-delegate-target"]:checked')?.value || contacts[0].email;
  const _note = () => modal.querySelector('#ib-delegate-note')?.value || '';

  // Preview = call /delegate-suggest without opening Mail.
  //
  // Backend now returns a 4-section analysis (full_text) plus the
  // employee-task body (intro). The preview block shows BOTH so the
  // manager sees the full reasoning above the email body that will be
  // sent to the colleague.
  async function _doPreview() {
    const btn = modal.querySelector('#ib-delegate-preview-btn');
    btn.disabled = true; btn.textContent = '⏳';
    try {
      const res = await api.inboxDelegateSuggest(item.id, _selectedEmail(), _note());
      modal.querySelector('#ib-delegate-preview').style.display = '';
      modal.querySelector('#ib-delegate-preview-subject').textContent = res.subject;
      // Body of the email being sent to the colleague (extracted from
      // the LLM's «ЧЕРНОВИК ЗАДАЧИ ДЛЯ СОТРУДНИКА» section).
      modal.querySelector('#ib-delegate-preview-intro').textContent = res.intro;
      // Optional full analysis (РЕКОМЕНДАЦИЯ / КОНТЕКСТ / ЧЕРНОВИК /
      // ПРИМЕЧАНИЕ) — shown collapsed below the intro inside a <details>
      // so the manager can drill into the reasoning without it dominating
      // the modal.
      const analysisEl    = modal.querySelector('#ib-delegate-preview-analysis');
      const analysisWrap  = modal.querySelector('#ib-delegate-preview-analysis-wrap');
      const showAnalysis  = res.full_text && res.full_text.trim() !== (res.intro || '').trim();
      if (analysisWrap) analysisWrap.style.display = showAnalysis ? '' : 'none';
      if (analysisEl)   analysisEl.textContent = showAnalysis ? res.full_text : '';
      modal.querySelector('#ib-delegate-preview-flag').textContent =
        res.mlx_used ? '🤖 Сгенерировано MLX' : '⚙️ Шаблон без MLX (модель недоступна)';
      return res;
    } catch (err) {
      _ctx?.showToast?.('Ошибка генерации: ' + err.message, 'error');
      return null;
    } finally {
      btn.disabled = false; btn.textContent = '👁 Предпросмотр';
    }
  }

  modal.querySelector('#ib-delegate-preview-btn')?.addEventListener('click', _doPreview);

  // Send = generate (if not yet) + POST /api/chat/save-draft-mail
  modal.querySelector('#ib-delegate-send')?.addEventListener('click', async () => {
    const sendBtn = modal.querySelector('#ib-delegate-send');
    sendBtn.disabled = true; sendBtn.textContent = '⏳';
    try {
      const res = await api.inboxDelegateSuggest(item.id, _selectedEmail(), _note());
      // Forward the pre-built draft payload to the existing save-draft-mail
      // endpoint — this opens Mail.app's compose window with To, subject,
      // and body all pre-filled.
      const mailRes = await fetch('/api/chat/save-draft-mail', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(res.draft_payload),
      });
      if (!mailRes.ok) {
        const err = await mailRes.json().catch(() => ({ detail: mailRes.statusText }));
        throw new Error(err.detail || mailRes.statusText);
      }
      const out = await mailRes.json();
      _ctx?.showToast?.(out.message || 'Черновик открыт в Mail', 'success');
      close();
    } catch (err) {
      _ctx?.showToast?.('Ошибка: ' + err.message, 'error');
      sendBtn.disabled = false; sendBtn.textContent = '✉️ Открыть в Mail';
    }
  });

  // ESC closes
  modal.addEventListener('keydown', e => {
    if (e.key === 'Escape') close();
  });
}

// ---------------------------------------------------------------------------
// Actions
// ---------------------------------------------------------------------------
function handleAction(action) {
  const item = _items[_activeIdx];
  if (!item) return;

  switch (action) {
    case 'draft':
      _openDraftWithContext(item);
      break;

    case 'summarize':
      // E-2 fix: use pa:chat-send (auto-send) instead of pa:chat-open (fill only).
      // Summarize doesn't need user to review the prompt — fire immediately.
      document.dispatchEvent(new CustomEvent('pa:chat-send', {
        detail: {
          message: `Суммаризируй тред писем по теме «${item.subject}»`,
          mode: 'chat',
          vault_thread_id: item.thread_id || null,
          reply_message_id: item.id,
        },
      }));
      _ctx?.activateTab?.('chat');
      break;

    case 'delegate':
      _openDelegatePicker(item);
      break;

    case 'create-meeting':
      // First try smart slot picker; fall back to chat if vault not ready
      _suggestMeeting(item);
      break;

    case 'to-project':
      // Scroll to project selector in assistant panel
      $('ib-project-select')?.focus();
      $('ib-project-select')?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
      break;

    case 'toggle-read': {
      const willBeRead = !item.read;
      _serverSetRead(item.id, willBeRead);
      item.read = willBeRead;
      renderList();
      renderAssistant();
      updateHeader();
      _ctx?.showToast?.(willBeRead ? 'Прочитано' : 'Непрочитано', 'success');
      break;
    }

    case 'archive':
      _serverSetRead(item.id, true);
      item.read = true;
      _items.splice(_activeIdx, 1);
      _activeIdx = Math.min(_activeIdx, _items.length - 1);
      renderList();
      renderDetail();
      renderAssistant();
      updateHeader();
      _ctx?.showToast?.('Архивировано', 'success');
      break;

    case 'snooze':
      _serverSetRead(item.id, true);
      item.read = true;
      renderList();
      renderAssistant();
      updateHeader();
      _ctx?.showToast?.('Отложено до завтра', 'info');
      break;
  }
}

// ---------------------------------------------------------------------------
// Meeting slot suggestion
// ---------------------------------------------------------------------------

/**
 * Call /api/v1/inbox/{id}/suggest-meeting, then show an inline slot picker
 * in the assistant panel.  Falls back to the chat tab if the API fails.
 */
async function _suggestMeeting(item) {
  const btn = document.querySelector('.ib-action[data-action="create-meeting"]');
  const origLabel = btn ? btn.textContent : null;
  if (btn) { btn.textContent = '⏳'; btn.disabled = true; }

  try {
    const res = await fetch(`/api/v1/inbox/${item.id}/suggest-meeting`, { method: 'POST' });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();

    // Render slot picker inside the assistant panel
    _renderSlotPicker(item, data);
  } catch (err) {
    console.warn('[inbox] suggest-meeting failed, falling back to chat:', err);
    // Graceful fallback: open chat with the original prompt
    document.dispatchEvent(new CustomEvent('pa:chat-send', {
      detail: {
        message: `Создай событие в календаре по письму «${item.subject}»`,
        mode: 'chat',
        vault_thread_id: item.thread_id || null,
        reply_message_id: item.id,
      },
    }));
    _ctx?.activateTab?.('chat');
  } finally {
    if (btn) { btn.textContent = origLabel || '📅'; btn.disabled = false; }
  }
}

/**
 * Inject a meeting slot picker block into the assistant panel.
 * Clicking a slot calls /api/v1/calendar/create-from-text (confirmed=true).
 */
function _renderSlotPicker(item, data) {
  const panel = document.getElementById('ib-assistant');
  if (!panel) return;

  // Remove any existing slot picker
  panel.querySelector('.ib-slot-picker')?.remove();

  const slots = data.slots || [];
  const title = data.title || `Встреча: ${item.subject}`;
  const participants = (data.participants || []).slice(0, 5).join(', ');

  let slotsHtml = '';
  if (slots.length === 0) {
    slotsHtml = '<p class="ib-slot-empty">Свободных слотов не найдено — расписание занято.</p>';
  } else {
    slotsHtml = slots.map((s, i) => `
      <button class="ib-slot-btn" data-idx="${i}"
              data-start="${s.start_iso}" data-end="${s.end_iso}"
              data-title="${title.replace(/"/g, '&quot;')}">
        ${s.display_str}
      </button>`).join('');
  }

  const picker = document.createElement('div');
  picker.className = 'ib-slot-picker';
  picker.innerHTML = `
    <div class="ib-slot-header">
      <span class="ib-slot-title">📅 ${title}</span>
      <button class="ib-slot-close" title="Закрыть">✕</button>
    </div>
    ${participants ? `<div class="ib-slot-participants">👥 ${participants}</div>` : ''}
    <div class="ib-slot-list">${slotsHtml}</div>
    <div class="ib-slot-fallback">
      <button class="ib-slot-chat-btn">Обсудить в чате</button>
    </div>`;

  // Close button
  picker.querySelector('.ib-slot-close').addEventListener('click', () => picker.remove());

  // Slot confirm buttons
  picker.querySelectorAll('.ib-slot-btn').forEach(btn => {
    btn.addEventListener('click', async () => {
      const slotTitle = btn.dataset.title;
      const startIso = btn.dataset.start;
      const endIso = btn.dataset.end;
      btn.textContent = '⏳ Создаём…';
      btn.disabled = true;
      try {
        const body = {
          text: `${slotTitle} ${startIso}`,
          confirmed: true,
          dry_run: false,
        };
        const r = await fetch('/api/v1/calendar/create-from-text', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        });
        const result = await r.json();
        if (result.created) {
          _ctx?.showToast?.('Событие создано в Календаре ✓', 'success');
          picker.remove();
        } else {
          btn.textContent = '⚠️ Ошибка';
          btn.disabled = false;
          console.warn('[inbox] create-from-text error:', result.error);
          _ctx?.showToast?.('Ошибка создания события', 'error');
        }
      } catch (e) {
        btn.textContent = '⚠️ Ошибка';
        btn.disabled = false;
        console.warn('[inbox] calendar API error:', e);
      }
    });
  });

  // "Обсудить в чате" fallback
  picker.querySelector('.ib-slot-chat-btn').addEventListener('click', () => {
    picker.remove();
    document.dispatchEvent(new CustomEvent('pa:chat-send', {
      detail: {
        message: `Создай событие в календаре по письму «${item.subject}»`,
        mode: 'chat',
        vault_thread_id: item.thread_id || null,
        reply_message_id: item.id,
      },
    }));
    _ctx?.activateTab?.('chat');
  });

  // Prepend to assistant panel (above suggestions)
  panel.insertBefore(picker, panel.firstChild);
  picker.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

// ---------------------------------------------------------------------------
// Server read/unread helpers
// ---------------------------------------------------------------------------
function _serverSetRead(id, isRead) {
  // Fire-and-forget — no need to await for optimistic UI
  const fn = isRead ? api.inboxMarkRead : api.inboxMarkUnread;
  fn(id).catch(e => console.warn('[inbox] set-read failed:', e));
}

// ---------------------------------------------------------------------------
// Item selection
// ---------------------------------------------------------------------------
function selectItem(idx) {
  if (idx < 0 || idx >= _items.length) return;
  _activeIdx = idx;
  const item = _items[idx];

  // Mark as read if not already (optimistic + server)
  if (!item.read) {
    item.read = true;
    _serverSetRead(item.id, true);
  }

  // Update active class in list without full re-render
  document.querySelectorAll('.ib-item').forEach((el, i) => {
    el.classList.toggle('ib-item--active', i === idx);
    if (i === idx) el.classList.add('ib-item--read');
  });
  // Also remove unread dot
  const activeEl = document.querySelector('.ib-item--active');
  activeEl?.querySelector('.ib-unread-dot')?.remove();

  renderDetail();
  renderAssistant();
  updateHeader();

  // Trigger background extraction if not yet available
  if (!item.extraction && _extractCache[item.id] === undefined) {
    loadExtraction(item);
  }
}

// ---------------------------------------------------------------------------
// Header / stats
// ---------------------------------------------------------------------------
function updateHeader() {
  const unread   = _items.filter(it => !it.read).length;
  const followup = _stats.followup || 0;

  const el = $('ib-header-stats');
  if (el) {
    const parts = [];
    if (unread > 0)    parts.push(`${unread} непрочитанных`);
    if (_stats.urgent > 0) parts.push(`${_stats.urgent} срочных`);
    if (followup > 0)  parts.push(`${followup} ждут ответа`);
    el.textContent = parts.join(' · ');
  }

  // Update urgency pills
  const urgentBadge    = $('ib-badge-urgent');
  const importantBadge = $('ib-badge-important');
  if (urgentBadge) {
    urgentBadge.textContent = _stats.urgent ? `${_stats.urgent} срочных` : '';
    urgentBadge.style.display = _stats.urgent ? '' : 'none';
  }
  if (importantBadge) {
    importantBadge.textContent = _stats.important ? `${_stats.important} важных` : '';
    importantBadge.style.display = _stats.important ? '' : 'none';
  }

  // Update followup filter tab badge
  const followupTab = document.querySelector('.ib-filter-tab--followup');
  if (followupTab) {
    const label = followup > 0 ? `🔔 Ответить (${followup})` : '🔔 Ответить';
    followupTab.textContent = label;
  }

  // Update nav badge — count both unread and followup
  const navBadge = $('nav-badge-inbox');
  const badgeCount = Math.max(unread, followup);
  if (navBadge) {
    navBadge.textContent = badgeCount || '';
    navBadge.style.display = badgeCount ? '' : 'none';
  }
}

// ---------------------------------------------------------------------------
// Load inbox from API
// ---------------------------------------------------------------------------
async function loadInbox(filter = _filter, sortBy = _sortBy) {
  _filter  = filter;
  _sortBy  = sortBy;
  _loading = true;
  renderList();

  try {
    const data = await api.inboxList(filter, 200, 0, sortBy);
    _items = data.items || [];
    _stats = data.stats || { total: 0, unread: 0, urgent: 0, important: 0, followup: 0 };

    if (_items.length > 0 && _activeIdx < 0) {
      _activeIdx = 0;
    } else if (_activeIdx >= _items.length) {
      _activeIdx = _items.length > 0 ? 0 : -1;
    }
  } catch (err) {
    console.warn('[inbox] load failed:', err);
    _items = [];
    _stats = { total: 0, unread: 0, urgent: 0, important: 0, followup: 0 };
  }

  _loading = false;
  renderList();
  renderDetail();
  renderAssistant();
  updateHeader();
}

// ---------------------------------------------------------------------------
// Keyboard shortcuts
// ---------------------------------------------------------------------------
function handleKey(e) {
  const panel = document.querySelector('.tab-panel[data-tab="inbox"]');
  if (!panel?.classList.contains('tab-panel--active')) return;

  const tag = (document.activeElement?.tagName || '').toLowerCase();
  if (tag === 'input' || tag === 'textarea' || tag === 'select') return;

  const k = e.key.toUpperCase();

  switch (k) {
    case 'J':
      e.preventDefault();
      selectItem(_activeIdx + 1);
      scrollActiveIntoView();
      break;
    case 'K':
      e.preventDefault();
      selectItem(Math.max(0, _activeIdx - 1));
      scrollActiveIntoView();
      break;
    case 'R':
      e.preventDefault();
      handleAction('draft');
      break;
    case 'D':
      e.preventDefault();
      handleAction('delegate');
      break;
    case 'U':
      e.preventDefault();
      handleAction('summarize');
      break;
    case 'E':
      e.preventDefault();
      handleAction('archive');
      break;
    case 'P':
      e.preventDefault();
      handleAction('to-project');
      break;
    case 'S':
      e.preventDefault();
      handleAction('snooze');
      break;
    case 'C':
      e.preventDefault();
      handleAction('create-meeting');
      break;
    case 'M':
      e.preventDefault();
      handleAction('toggle-read');
      break;
  }
}

function scrollActiveIntoView() {
  const active = document.querySelector('.ib-item--active');
  active?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
}

// ---------------------------------------------------------------------------
// Quick-search input
// ---------------------------------------------------------------------------
function setupSearchInput() {
  const input = document.getElementById('ib-search-input');
  const clear = document.getElementById('ib-search-clear');
  if (!input) return;

  // Debounce so we don't re-render on every keystroke in 500-item inboxes.
  let debounceId = null;
  const apply = () => {
    _search = input.value || '';
    if (clear) clear.style.display = _search.trim() ? '' : 'none';
    _activeIdx = -1;
    renderList();
    renderDetail();
  };
  const schedule = () => {
    if (debounceId) clearTimeout(debounceId);
    debounceId = setTimeout(apply, 120);
  };

  input.addEventListener('input', schedule);
  input.addEventListener('keydown', e => {
    if (e.key === 'Escape') {
      input.value = '';
      apply();
      input.blur();
    }
  });
  clear?.addEventListener('click', () => {
    input.value = '';
    apply();
    input.focus();
  });

  // "/" focuses the search box from anywhere in the inbox tab (skip when
  // user is already typing in another input/textarea).
  document.addEventListener('keydown', e => {
    if (e.key !== '/' || e.ctrlKey || e.metaKey || e.altKey) return;
    const tag = (e.target?.tagName || '').toLowerCase();
    if (tag === 'input' || tag === 'textarea' || e.target?.isContentEditable) return;
    const panel = document.querySelector('.tab-panel[data-tab="inbox"].tab-panel--active');
    if (!panel) return;
    e.preventDefault();
    input.focus();
    input.select();
  });
}

// ---------------------------------------------------------------------------
// Filter tabs
// ---------------------------------------------------------------------------
function setupFilterTabs() {
  document.querySelectorAll('.ib-filter-tab').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.ib-filter-tab')
        .forEach(b => b.classList.remove('ib-filter-tab--active'));
      btn.classList.add('ib-filter-tab--active');
      _activeIdx = -1;
      loadInbox(btn.dataset.filter, _sortBy);
    });
  });

  // Sort toggle (by date / by priority)
  const sortBtn = document.getElementById('ib-sort-toggle');
  if (sortBtn) {
    sortBtn.addEventListener('click', () => {
      _sortBy = _sortBy === 'date' ? 'priority' : 'date';
      sortBtn.textContent = _sortBy === 'priority' ? '↓ Приоритет' : '↓ Дата';
      sortBtn.classList.toggle('ib-sort-toggle--active', _sortBy === 'priority');
      _activeIdx = -1;
      loadInbox(_filter, _sortBy);
    });
  }

  // Thread-grouping toggle (Outlook Conversations view).
  const groupBtn = document.getElementById('ib-group-toggle');
  if (groupBtn) {
    groupBtn.addEventListener('click', () => {
      _groupByThread = !_groupByThread;
      groupBtn.classList.toggle('ib-group-toggle--active', _groupByThread);
      groupBtn.textContent = _groupByThread ? '🧵 Беседы' : '☰ Список';
      groupBtn.title = _groupByThread
        ? 'Сейчас: треды свёрнуты по теме (как в Outlook «Беседы»). Кликни, чтобы переключиться на плоский список.'
        : 'Сейчас: плоский список. Кликни, чтобы сгруппировать письма по треду.';
      // Reset expanded state when switching modes — start clean
      _expandedThreads.clear();
      renderList();
    });
  }

  // Mark-all-read toolbar button: bulk-update every currently-visible
  // unread item via one /mark-read-batch call.  After success, the
  // affected items are flipped locally so the user sees instant feedback
  // without waiting for a full inbox reload.
  const markAllBtn = document.getElementById('ib-mark-all-read');
  if (markAllBtn) {
    markAllBtn.addEventListener('click', async () => {
      const visible = _filteredItems().filter(it => !it.read);
      if (!visible.length) {
        _ctx?.showToast?.('Все письма уже прочитаны', 'info');
        return;
      }
      const ids = visible.map(it => it.id);
      const orig = markAllBtn.textContent;
      markAllBtn.disabled = true;
      markAllBtn.textContent = '⏳';
      try {
        const res = await api.inboxMarkReadBatch(ids, true);
        // Flip read flag locally (mutate _items so renders + stats update)
        const idSet = new Set(ids);
        _items.forEach(it => { if (idSet.has(it.id)) it.read = true; });
        renderList();
        _ctx?.showToast?.(`Отмечено прочитанными: ${res.updated || ids.length}`, 'success');
      } catch (err) {
        _ctx?.showToast?.('Ошибка: ' + err.message, 'error');
      } finally {
        markAllBtn.disabled = false;
        markAllBtn.textContent = orig;
      }
    });
  }
}

// ---------------------------------------------------------------------------
// Email body → HTML
// ---------------------------------------------------------------------------
//
// Renders the stored mail body the way real email clients do: quoted
// replies indented as blockquotes, forwarded-message banners visually
// separated, signatures muted, lists/headers/links/inline-code parsed.
//
// We deliberately do NOT pull a markdown library in — vault bodies are
// AppleScript-extracted plain text with some markdown leakage from AI
// summaries, not full Commonmark.  A focused parser is faster and yields
// cleaner output for the email use case.

// Forwarded-message banners across English / Russian Mail clients.
const _FORWARD_HEADER_RE = new RegExp(
  '^(' +
    '-{2,}\\s*(forwarded message|пересланное сообщение|переадресованное сообщение|begin forwarded message)[\\s:-]*' +
  '|' +
    '={3,}\\s*(forwarded|переслано)' +
  '|' +
    'begin forwarded message:?' +
  ')$',
  'i'
);

// "On Mon, May 26, 2026 at 14:30, Alice wrote:" / "Алиса написала:" /
// "26 мая 2026 г., в 14:30, Алиса написал(а):"
const _REPLY_INTRO_RE = new RegExp(
  '^(?:on .+ wrote:?$' +
  '|.+ написал(?:\\(а\\)|а)?:$' +
  '|.+ writes?:$' +
  '|le .+ a écrit\\s*:$' +
  '|.+ schrieb am .+:$' +
  ')',
  'i'
);

// Auto-link URLs and bare emails.  Excludes trailing punctuation.
const _URL_RE   = /\b(https?:\/\/[^\s<>"']+[^\s<>"',.;:!?)\]}])/g;
const _EMAIL_RE = /\b([\w._%+-]+@[\w.-]+\.[a-z]{2,})\b/gi;

/** Apply inline markdown + auto-links on already-escaped text. */
function _inline(text) {
  // Inline code first so other patterns don't run inside.
  text = text.replace(/`([^`\n]+)`/g, '<code>$1</code>');
  text = text.replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>');
  text = text.replace(/\b_([^_\n]+)_\b/g, '<em>$1</em>');
  text = text.replace(/(?<![*\w])\*([^*\n]+)\*(?!\w)/g, '<em>$1</em>');
  text = text.replace(/~~([^~\n]+)~~/g, '<del>$1</del>');
  // [label](url) — must run before bare URL autolinking
  text = text.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g,
    (_m, label, url) => `<a href="${url}" target="_blank" rel="noopener">${label}</a>`);
  // Bare URLs
  text = text.replace(_URL_RE,
    (_m, url) => `<a href="${url}" target="_blank" rel="noopener">${url}</a>`);
  // Bare emails → mailto
  text = text.replace(_EMAIL_RE,
    (_m, email) => `<a href="mailto:${email}">${email}</a>`);
  return text;
}

/** Strip leading "> " from a line, repeatedly, returning {depth, rest}. */
function _stripQuote(line) {
  let depth = 0;
  while (true) {
    const m = line.match(/^>\s?(.*)$/);
    if (!m) break;
    depth += 1;
    line = m[1];
  }
  return { depth, rest: line };
}

/** Convert one logical block of paragraph text to <p>…</p> with <br> for
 *  internal single newlines.  Email bodies often have soft wraps. */
function _paragraphHtml(lines) {
  if (!lines.length) return '';
  const txt = lines.join('\n').trim();
  if (!txt) return '';
  // Escape, then apply inline rules, then turn \n into <br>.
  const escaped = _esc(txt);
  const withInline = _inline(escaped);
  return `<p>${withInline.replace(/\n/g, '<br>')}</p>`;
}

/** Email-aware text → HTML renderer (export-shaped so we can also unit-test it). */
function _emailToHtml(raw) {
  if (!raw) return '';

  // Normalise line endings + collapse 4+ blank lines into 2 (some Mail
  // clients pad with spam-like vertical space).
  const text = String(raw)
    .replace(/\r\n?/g, '\n')
    .replace(/[ \t]+\n/g, '\n')
    .replace(/\n{4,}/g, '\n\n\n');

  const lines = text.split('\n');
  const out = [];

  // Block-level state machine
  let para = [];        // accumulating paragraph lines
  let quoteBuf = [];    // accumulating quoted lines (raw, after one strip)
  let quoteDepth = 0;
  let listType = null;  // 'ul' | 'ol' | null
  let listItems = [];
  let codeFence = null; // current code-block content lines
  let signatureMode = false;

  const flushPara = () => {
    if (para.length) {
      out.push(_paragraphHtml(para));
      para = [];
    }
  };
  const flushQuote = () => {
    if (quoteBuf.length) {
      const inner = _emailToHtml(quoteBuf.join('\n'));
      out.push(`<blockquote class="ib-email-quote ib-email-quote--d${Math.min(quoteDepth,4)}">${inner}</blockquote>`);
      quoteBuf = [];
      quoteDepth = 0;
    }
  };
  const flushList = () => {
    if (listType && listItems.length) {
      const tag = listType;
      const items = listItems.map(item =>
        `<li>${_inline(_esc(item))}</li>`).join('');
      out.push(`<${tag}>${items}</${tag}>`);
      listType = null;
      listItems = [];
    }
  };
  const flushAll = () => { flushPara(); flushQuote(); flushList(); };

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const trimmed = line.trim();

    // ── Code fence ───────────────────────────────────────────────────────
    if (codeFence !== null) {
      if (/^```\s*$/.test(trimmed)) {
        out.push(`<pre class="ib-email-code"><code>${_esc(codeFence.join('\n'))}</code></pre>`);
        codeFence = null;
      } else {
        codeFence.push(line);
      }
      continue;
    }
    if (/^```/.test(trimmed)) {
      flushAll();
      codeFence = [];
      continue;
    }

    // ── Signature separator "-- " (canonical: exactly two dashes) ───────
    // Strict: only "--" after trim — ``---`` and longer are horizontal rules.
    if (trimmed === '--') {
      flushAll();
      signatureMode = true;
      out.push('<div class="ib-email-signature">');
      continue;
    }

    // ── Forwarded-message banner ─────────────────────────────────────────
    if (_FORWARD_HEADER_RE.test(trimmed)) {
      flushAll();
      out.push(`<div class="ib-email-forward-header">↪ ${_esc(trimmed.replace(/^-+\s*|\s*-+$/g, ''))}</div>`);
      continue;
    }

    // ── Inline reply-intro ("On Mon, … wrote:" / "Алиса написала:") ──────
    if (_REPLY_INTRO_RE.test(trimmed)) {
      flushAll();
      out.push(`<div class="ib-email-reply-intro">${_esc(trimmed)}</div>`);
      continue;
    }

    // ── Quoted reply (one or more leading "> ") ─────────────────────────
    if (/^>/.test(trimmed)) {
      flushPara(); flushList();
      const { depth, rest } = _stripQuote(line.replace(/^\s+/, ''));
      if (quoteBuf.length && depth !== quoteDepth) {
        // Depth changed — flush previous block to render separate quotes
        flushQuote();
      }
      quoteDepth = depth;
      quoteBuf.push(rest);
      continue;
    }

    // ── Horizontal rule ──────────────────────────────────────────────────
    if (/^(?:-{3,}|_{3,}|\*{3,})$/.test(trimmed)) {
      flushAll();
      out.push('<hr class="ib-email-hr">');
      continue;
    }

    // ── Headings (only ## and ###; # is too greedy in plain emails) ──────
    const hm = trimmed.match(/^(#{2,3})\s+(.+)$/);
    if (hm) {
      flushAll();
      const level = hm[1].length;
      out.push(`<h${level} class="ib-email-h${level}">${_inline(_esc(hm[2]))}</h${level}>`);
      continue;
    }

    // ── Unordered list "- item" / "* item" / "• item" ────────────────────
    const um = line.match(/^\s*[-*•]\s+(.*)$/);
    if (um) {
      flushPara(); flushQuote();
      if (listType !== 'ul') { flushList(); listType = 'ul'; }
      listItems.push(um[1]);
      continue;
    }
    // ── Ordered list "1. item" / "1) item" ───────────────────────────────
    const om = line.match(/^\s*\d+[.)]\s+(.*)$/);
    if (om) {
      flushPara(); flushQuote();
      if (listType !== 'ol') { flushList(); listType = 'ol'; }
      listItems.push(om[1]);
      continue;
    }
    // If we were in a list and hit a non-list, non-empty line → close list
    if (listType && trimmed === '') { flushList(); continue; }
    if (listType && trimmed !== '') { flushList(); }

    // ── Blank line → paragraph boundary ──────────────────────────────────
    if (trimmed === '') {
      flushPara(); flushQuote();
      continue;
    }

    // ── Default: accumulate into current paragraph ───────────────────────
    flushQuote();
    para.push(line);
  }

  flushAll();
  if (codeFence !== null) {
    out.push(`<pre class="ib-email-code"><code>${_esc(codeFence.join('\n'))}</code></pre>`);
  }
  if (signatureMode) out.push('</div>');
  return out.join('\n');
}

// Kept as alias for any older callers / tests.
const _md2html = _emailToHtml;

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------
export function initInbox(ctx) {
  _ctx = ctx;

  setupFilterTabs();
  setupSearchInput();
  document.addEventListener('keydown', handleKey);

  // Reload when tab becomes active
  window.addEventListener('hashchange', () => {
    if (location.hash === '#inbox') loadInbox();
  });

  // Listen for inbox:open events from other tabs (e.g. Today)
  window.addEventListener('inbox:open', e => {
    const { id } = e.detail || {};
    if (id) {
      const idx = _items.findIndex(it => it.id === id);
      if (idx >= 0) selectItem(idx);
    }
    loadInbox();
  });

  // Initial load
  loadInbox();
}
