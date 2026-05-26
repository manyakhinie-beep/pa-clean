// =============================================================================
// search.js — Search tab: BM25 doc search, section + tag filters, tool buttons
// =============================================================================
import { api } from './api.js?v=20260520153419';

// Tool buttons per section — mirroring vault.js VAULT_TOOLS
const SEARCH_TOOLS = {
  mail: [
    { id: 'draft',     label: '✉️ Написать ответ',  mode: 'draft',     message: '/draft ' },
    { id: 'summarize', label: '📝 Суммаризировать',  mode: 'summarize', message: '/summarize ' },
  ],
  calendar: [
    { id: 'summarize', label: '📝 Суммаризировать',  mode: 'summarize', message: '/summarize ' },
    { id: 'chat',      label: '💬 Обсудить',         mode: 'chat',      message: 'Расскажи подробнее о встрече.' },
  ],
  contacts: [
    { id: 'summarize', label: '📝 Суммаризировать',  mode: 'summarize', message: '/summarize ' },
    { id: 'chat',      label: '💬 Обсудить',         mode: 'chat',      message: '' },
  ],
  default: [
    { id: 'summarize', label: '📝 Суммаризировать',  mode: 'summarize', message: '/summarize ' },
    { id: 'chat',      label: '💬 Обсудить',         mode: 'chat',      message: '' },
  ],
};

export function initSearch(ctx) {
  const { showToast, activateTab } = ctx;

  const inputEl    = document.getElementById('search-input');
  const resultsEl  = document.getElementById('search-results');
  const topKEl     = document.getElementById('search-top-k');
  const tagRow     = document.getElementById('search-tag-filters');
  const modeHint   = document.getElementById('search-mode-hint');

  if (!inputEl) return;

  let activeSection  = '';          // '' = all sections
  let activeTags     = new Set();   // active tag filters
  let activeMode     = 'bm25';      // 'bm25' | 'hybrid'
  let debounceTimer  = null;

  // ── Search mode toggle ────────────────────────────────────────────────
  const _MODE_HINTS = {
    bm25:   'Ранжирование по TF-IDF',
    hybrid: 'BM25 + keyword-fallback + поиск по вложениям и датам',
  };
  document.querySelectorAll('.search__filter-chip[data-mode]').forEach(chip => {
    chip.addEventListener('click', () => {
      document.querySelectorAll('.search__filter-chip[data-mode]').forEach(c =>
        c.classList.remove('search__filter-chip--active'));
      chip.classList.add('search__filter-chip--active');
      activeMode = chip.dataset.mode;
      if (modeHint) modeHint.textContent = _MODE_HINTS[activeMode] || '';
      doSearch();
    });
  });

  // ── Load tag chips for the tag filter row ────────────────────────────────
  // Shows ALL vault tags (classifier tags like "category:finance" first,
  // then simple tags like "meeting"), up to 40 total.
  async function loadTagFilters() {
    if (!tagRow) return;
    try {
      const data = await api.vaultTags();
      const allTags = (data.tags || []).slice(0, 40);
      if (!allTags.length) return;

      // Classifier tags first (contain ':'), then simple tags
      const classifierTags = allTags.filter(t => t.includes(':'));
      const simpleTags     = allTags.filter(t => !t.includes(':'));
      const ordered        = [...classifierTags, ...simpleTags];

      tagRow.innerHTML = '';

      const label = document.createElement('span');
      label.style.cssText = 'font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.05em;color:var(--color-text-faint);white-space:nowrap;align-self:center';
      label.textContent = 'Теги:';
      tagRow.appendChild(label);

      ordered.forEach(tag => {
        const btn = document.createElement('button');
        btn.className = 'search__filter-chip';
        // Dim simple tags slightly so classifier tags stand out
        if (!tag.includes(':')) btn.style.opacity = '0.75';
        btn.textContent = tag;
        btn.dataset.tag = tag;
        btn.addEventListener('click', () => {
          if (activeTags.has(tag)) {
            activeTags.delete(tag);
            btn.classList.remove('search__filter-chip--active');
          } else {
            activeTags.add(tag);
            btn.classList.add('search__filter-chip--active');
          }
          doSearch();
        });
        tagRow.appendChild(btn);
      });

      const clearBtn = document.createElement('button');
      clearBtn.style.cssText = 'margin-left:auto;padding:3px 10px;font-size:11px;color:var(--color-text-faint);background:none;border:1px solid var(--color-border);border-radius:99px;cursor:pointer;white-space:nowrap';
      clearBtn.textContent = '✕ Сбросить';
      clearBtn.addEventListener('click', () => {
        activeTags.clear();
        tagRow.querySelectorAll('.search__filter-chip[data-tag]').forEach(b =>
          b.classList.remove('search__filter-chip--active'));
        doSearch();
      });
      tagRow.appendChild(clearBtn);
      tagRow.style.display = 'flex';
    } catch { /* silently ignore */ }
  }

  // ── Section filter chips ──────────────────────────────────────────────────
  document.querySelectorAll('.search__filter-chip[data-section]').forEach(chip => {
    chip.addEventListener('click', () => {
      document.querySelectorAll('.search__filter-chip[data-section]').forEach(c =>
        c.classList.remove('search__filter-chip--active'));
      chip.classList.add('search__filter-chip--active');
      activeSection = chip.dataset.section;
      doSearch();
    });
  });

  // ── Input with debounce ───────────────────────────────────────────────────
  inputEl.addEventListener('input', () => {
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(() => doSearch(), 350);
  });

  inputEl.addEventListener('keydown', e => {
    if (e.key === 'Enter') { clearTimeout(debounceTimer); doSearch(); }
  });

  topKEl?.addEventListener('change', () => doSearch());

  // ── Core search ───────────────────────────────────────────────────────────
  async function doSearch() {
    const q     = inputEl.value.trim();
    const top_k = parseInt(topKEl?.value || '20', 10);

    if (!q && !activeSection && !activeTags.size) { showEmpty(); return; }

    showLoading();
    try {
      const body = { query: q, top_k, mode: activeMode };
      if (activeSection)   body.sections = [activeSection];
      if (activeTags.size) body.tags     = [...activeTags];

      const data = await api.searchDocs(body);
      renderResults(data.docs || [], q);
    } catch (err) {
      showToast('Ошибка поиска: ' + err.message, 'error');
      showEmpty('Ошибка поиска');
    }
  }

  // ── Rendering ─────────────────────────────────────────────────────────────
  function showEmpty(text) {
    resultsEl.innerHTML = '';
    const el = document.createElement('div');
    el.className = 'search__empty';
    el.innerHTML = '<div style="font-size:40px">🔍</div><p>' + (text || 'Введите запрос для поиска') + '</p>';
    resultsEl.appendChild(el);
  }

  function showLoading() {
    resultsEl.innerHTML = '<div class="search__loading">Поиск…</div>';
  }

  function escHtml(s) {
    return (s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  }

  // ── Classification tag pills ───────────────────────────────────────────────
  // Maps raw tag strings to {cls, label} matching rules-tag-pill--{cls} CSS
  const _TAG_PILL_MAP = {
    'urgency:urgent':    { cls: 'urgency-urgent',    label: 'Срочно' },
    'urgency:critical':  { cls: 'urgency-urgent',    label: 'Срочно' },
    'urgency:high':      { cls: 'urgency-urgent',    label: 'Срочно' },
    'urgency:important': { cls: 'urgency-important', label: 'Важно' },
    'urgency:medium':    { cls: 'urgency-important', label: 'Важно' },
    'urgency:low':       { cls: 'urgency-low',       label: 'Обычный' },
    'urgency:normal':    { cls: 'urgency-low',       label: 'Обычный' },
    'category:finance':  { cls: 'category-finance',  label: 'Финансы' },
    'category:meetings': { cls: 'category-meetings', label: 'Встречи' },
    'category:projects': { cls: 'category-projects', label: 'Проекты' },
    'category:hr':       { cls: 'category-hr',       label: 'HR' },
    'category:legal':    { cls: 'category-legal',    label: 'Юридическое' },
    'category:travel':   { cls: 'category-travel',   label: 'Командировки' },
  };

  function tagPillHtml(rawTag) {
    const key = rawTag.toLowerCase();
    const info = _TAG_PILL_MAP[key];
    if (info) {
      return `<span class="rules-tag-pill rules-tag-pill--${info.cls}" style="cursor:pointer" data-tag="${escHtml(rawTag)}">${info.label}</span>`;
    }
    if (rawTag.includes(':')) {
      // Auto-derive from unknown classifier tag
      const [kind, value] = rawTag.split(':', 2);
      const cls = `${kind}-${value.replace(/_/g, '-')}`;
      const label = value.replace(/[-_]/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
      return `<span class="rules-tag-pill rules-tag-pill--default" style="cursor:pointer" data-tag="${escHtml(rawTag)}">${escHtml(label)}</span>`;
    }
    // Plain tags — show as small badge, not pill
    return `<span class="badge" style="cursor:pointer" data-tag="${escHtml(rawTag)}">${escHtml(rawTag)}</span>`;
  }

  function highlightText(text, query) {
    let result = escHtml(text);
    if (!query) return result;
    query.trim().split(/\s+/).filter(Boolean).forEach(w => {
      const re = new RegExp('(' + w.replace(/[.*+?^${}()|[\]\\]/g,'\\$&') + ')', 'gi');
      result = result.replace(re, '<mark class="search__highlight">$1</mark>');
    });
    return result;
  }

  function renderResults(docs, query) {
    resultsEl.innerHTML = '';
    if (!docs.length) { showEmpty('Ничего не найдено'); return; }

    const summary = document.createElement('div');
    summary.style.cssText = 'font-size:12px;color:var(--color-text-faint);padding:8px 0 10px';
    summary.textContent = 'Найдено: ' + docs.length + ' документов';
    resultsEl.appendChild(summary);

    docs.forEach(item => {
      const section     = item.section || 'default';
      const title       = item.title || (item.path || '').split('/').pop() || 'Без названия';
      const date        = item.date || '';
      const snippet     = item.snippet || '';
      const tags        = item.tags || [];
      const attachments = item.attachments || [];
      const sectionIcon = { mail:'📧', calendar:'📅', contacts:'👤' }[section] || '📄';

      const el = document.createElement('div');
      el.className = 'search__result';
      el.style.cssText = 'cursor:pointer';

      // Tags HTML — classifier tags as pills, plain tags as small badges
      // Only show classifier tags (containing ':') as pills; limit to 4
      const classifierTags = tags.filter(t => t.includes(':'));
      const plainTags = tags.filter(t => !t.includes(':'));
      const visibleTags = [...classifierTags.slice(0, 3), ...plainTags.slice(0, 2)];
      const tagsHtml = visibleTags.length
        ? visibleTags.map(t => tagPillHtml(t)).join('')
        : '';

      // Attachments HTML — show file icons + highlighted names
      const attHtml = attachments.length
        ? '<div style="display:flex;flex-wrap:wrap;gap:4px;margin-top:5px">' +
            attachments.map(a =>
              '<span style="font-size:11px;color:var(--color-text-muted);background:var(--color-bg-subtle,var(--color-bg));border:1px solid var(--color-border);border-radius:4px;padding:1px 6px;display:inline-flex;align-items:center;gap:3px">' +
              '📎 ' + highlightText(a, query) + '</span>'
            ).join('') +
          '</div>'
        : '';

      el.innerHTML =
        '<div class="search__result-header" style="display:flex;align-items:center;gap:8px;flex-wrap:nowrap">' +
          '<span style="font-size:15px;flex-shrink:0">' + sectionIcon + '</span>' +
          '<span style="font-size:14px;font-weight:600;color:var(--color-text);flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' + highlightText(title, query) + '</span>' +
          (date ? '<span style="font-size:11px;color:var(--color-text-faint);white-space:nowrap;flex-shrink:0">' + escHtml(date) + '</span>' : '') +
        '</div>' +
        (snippet ? '<div class="search__result-snippet" style="font-size:13px;color:var(--color-text-muted);margin-top:4px;line-height:1.5">' + highlightText(snippet, query) + '</div>' : '') +
        attHtml +
        (tagsHtml ? '<div style="display:flex;flex-wrap:wrap;gap:4px;margin-top:6px">' + tagsHtml + '</div>' : '') +
        '<div class="search__result-actions" style="display:flex;gap:6px;margin-top:8px;flex-wrap:wrap"></div>';

      // Tag click → add to filter (all tags, not just classifier ones)
      el.querySelectorAll('[data-tag]').forEach(tagEl => {
        tagEl.addEventListener('click', e => {
          e.stopPropagation();
          const tag = tagEl.dataset.tag;
          if (!activeTags.has(tag)) {
            activeTags.add(tag);
            const rowBtn = tagRow && tagRow.querySelector('.search__filter-chip[data-tag="' + CSS.escape(tag) + '"]');
            if (rowBtn) rowBtn.classList.add('search__filter-chip--active');
            doSearch();
          }
        });
      });

      // Tool action buttons
      //
      // Critical: each tool must carry the doc's id/thread_id/path through to
      // chat so reply_to_message_id and vault context are correctly wired.
      // The previous implementation only passed `path` + an empty `/draft `
      // slash-stub, which left the chat with NULL reply context — so the
      // draft button created a NEW email instead of threading into the
      // existing message, and summarize had no document to summarize.
      const actionsEl = el.querySelector('.search__result-actions');
      const tools = SEARCH_TOOLS[section] || SEARCH_TOOLS.default;
      tools.forEach(tool => {
        const btn = document.createElement('button');
        btn.className = 'btn btn--sm btn--secondary';
        btn.textContent = tool.label;
        btn.addEventListener('click', async e => {
          e.stopPropagation();
          await _runTool(tool, item, title, btn);
        });
        actionsEl.appendChild(btn);
      });

      // Click on card → open in vault detail
      el.addEventListener('click', () => {
        activateTab('vault');
        document.dispatchEvent(new CustomEvent('vault:open', { detail: { path: item.path } }));
      });

      resultsEl.appendChild(el);
    });
  }

  // ── Tool dispatcher ───────────────────────────────────────────────────────
  //
  // Builds the right chat payload for a search-result action.  Mirrors what
  // inbox.js does so the chat receives reply_to_message_id, vault_thread_id
  // and a meaningful prompt string, not a bare "/draft ".
  async function _runTool(tool, item, title, btn) {
    const section = item.section || 'default';
    const replyId = item.id || null;     // file stem / id frontmatter — backend resolves
    const threadId = item.thread_id || null;

    // ── Mail draft → reply to existing thread with full context ────────────
    if (tool.mode === 'draft' && section === 'mail' && replyId) {
      const origLabel = btn.textContent;
      btn.disabled = true;
      btn.textContent = '⏳';
      try {
        const ctx = await api.inboxDraftContext(replyId);
        const message =
          ctx.context_prompt ||
          `Составь черновик ответа на письмо от ${item.sender_name || '—'}: «${item.subject || title}»`;
        activateTab('chat');
        document.dispatchEvent(new CustomEvent('pa:chat-open', {
          detail: {
            path: item.path || null,
            title,
            mode: 'draft',
            message,
            vault_thread_id: threadId,
            reply_message_id: replyId,
            thread_context: ctx,
          },
        }));
      } catch (err) {
        // Fallback: open chat with reply context but no LLM prep
        activateTab('chat');
        document.dispatchEvent(new CustomEvent('pa:chat-open', {
          detail: {
            path: item.path || null,
            title,
            mode: 'draft',
            message: `Составь черновик ответа на письмо: «${item.subject || title}»`,
            vault_thread_id: threadId,
            reply_message_id: replyId,
          },
        }));
        showToast('Контекст треда недоступен — открыт чат без подготовки', 'warning');
      } finally {
        btn.disabled = false;
        btn.textContent = origLabel;
      }
      return;
    }

    // ── Summarize → fire pa:chat-send so the prompt runs immediately ───────
    if (tool.mode === 'summarize') {
      const subj = item.subject || title;
      const message =
        section === 'mail'
          ? `Суммаризируй тред писем по теме «${subj}»`
          : section === 'calendar'
            ? `Суммаризируй встречу «${subj}» и подскажи ключевые моменты`
            : `Суммаризируй документ «${subj}»`;
      activateTab('chat');
      document.dispatchEvent(new CustomEvent('pa:chat-send', {
        detail: {
          message,
          mode: 'chat',
          vault_thread_id: threadId,
          reply_message_id: replyId,
          path: item.path || null,
        },
      }));
      return;
    }

    // ── Generic chat / discuss ─────────────────────────────────────────────
    const message = tool.message && tool.message.trim()
      ? tool.message
      : `Расскажи подробнее о «${item.subject || title}»`;
    activateTab('chat');
    document.dispatchEvent(new CustomEvent('pa:chat-open', {
      detail: {
        path: item.path || null,
        title,
        mode: tool.mode || 'chat',
        message,
        vault_thread_id: threadId,
        reply_message_id: replyId,
      },
    }));
  }

  // ── Focus & lazy-load tags on tab activate ────────────────────────────────
  document.querySelectorAll('.nav__item[data-tab="search"]').forEach(btn => {
    btn.addEventListener('click', () => {
      setTimeout(() => inputEl.focus(), 100);
      if (tagRow && !tagRow.children.length) loadTagFilters();
    });
  });


  // ── pa:tags-reset — Settings fired after reset+reclassify ─────────────────
  document.addEventListener('pa:tags-reset', async () => {
    activeTags.clear();
    activeSection = '';
    document.querySelectorAll('.search__filter-chip[data-section]').forEach(c =>
      c.classList.toggle('search__filter-chip--active', c.dataset.section === ''));
    await loadTagFilters();
    showEmpty();
  });

  // ── pa:vault-reloaded — fired after sync / vault reload in settings ───────
  document.addEventListener('pa:vault-reloaded', async () => {
    await loadTagFilters();
    // Re-run search if there's an active query
    const q = inputEl.value.trim();
    if (q || activeSection || activeTags.size) await doSearch();
  });

  // ── Init ──────────────────────────────────────────────────────────────────
  loadTagFilters();
  showEmpty();
}

