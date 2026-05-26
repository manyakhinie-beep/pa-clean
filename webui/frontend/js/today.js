// =============================================================================
// today.js — «Сегодня» dashboard tab
// =============================================================================
import { api } from './api.js?v=20260520153419';

// Suggestion icon map
const _SUGGESTION_ICONS = {
  draft:     '✏️',
  summarize: '📋',
  chat:      '💬',
  brief:     '📄',
  focus:     '🎯',
};

export function initToday(ctx) {
  const { showToast, activateTab } = ctx;

  // ── DOM refs ────────────────────────────────────────────────────────────────
  const dateLabel        = document.getElementById('today-date-label');
  const greetingEl       = document.getElementById('today-greeting');
  const bulletsEl        = document.getElementById('today-bullets');
  const updatedAtEl      = document.getElementById('today-updated-at');
  const nextUpdateEl     = document.getElementById('today-next-update');
  const eventsListEl     = document.getElementById('today-events-list');
  const eventsCountEl    = document.getElementById('today-events-count');
  const attentionListEl  = document.getElementById('today-attention-list');
  const attentionCountEl = document.getElementById('today-attention-count');
  const attentionFooter  = document.getElementById('today-attention-footer');
  const attentionTotal   = document.getElementById('today-attention-total-label');
  const suggestionsListEl= document.getElementById('today-suggestions-list');
  const suggestionsCount = document.getElementById('today-suggestions-count');
  const searchInput      = document.getElementById('today-search-input');

  if (!greetingEl) return;   // panel not in DOM

  // ── Live clock ───────────────────────────────────────────────────────────────
  const _DAYS = ['вс','пн','вт','ср','чт','пт','сб'];
  const _MONTHS = ['янв','фев','мар','апр','мая','июн','июл','авг','сен','окт','ноя','дек'];

  function _updateClock() {
    if (!dateLabel) return;
    const now = new Date();
    const day  = _DAYS[now.getDay()];
    const d    = now.getDate();
    const mon  = _MONTHS[now.getMonth()];
    const hh   = String(now.getHours()).padStart(2, '0');
    const mm   = String(now.getMinutes()).padStart(2, '0');
    // Determine timezone abbreviation
    const tzName = Intl.DateTimeFormat('ru', {timeZoneName: 'short'})
      .formatToParts(now).find(p => p.type === 'timeZoneName')?.value || '';
    dateLabel.textContent = `${day}, ${d} ${mon} · ${hh}:${mm} ${tzName}`.trim();
  }

  _updateClock();
  setInterval(_updateClock, 30_000);

  // ── Helpers ───────────────────────────────────────────────────────────────────
  function _esc(s) {
    return (s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  }

  // ── Load data ─────────────────────────────────────────────────────────────────
  async function load() {
    try {
      const data = await api.today();
      render(data);
      // Non-blocking parallel loads
      loadUpcomingMeetings();
      loadDailyBrief();
    } catch (err) {
      greetingEl.textContent = 'Не удалось загрузить данные';
      showToast('Ошибка загрузки «Сегодня»: ' + err.message, 'error');
    }
  }

  // ── Daily Brief (Stage 6) ─────────────────────────────────────────────────────
  async function loadDailyBrief(forceRefresh = false) {
    const section = document.getElementById('today-brief-section');
    if (!section) return;
    try {
      const brief = await api.briefDaily(forceRefresh);
      renderDailyBrief(brief, section);
    } catch (_) {
      section.style.display = 'none';
    }
  }

  function renderDailyBrief(brief, section) {
    if (!brief || !brief.vault_loaded) {
      section.style.display = 'none';
      return;
    }
    section.style.display = '';

    // Insight text
    const insightEl = section.querySelector('.today__brief-insight');
    if (insightEl && brief.ai_insight) {
      insightEl.textContent = brief.ai_insight;
    }

    // Bullets
    const bulletsEl = section.querySelector('.today__brief-bullets');
    if (bulletsEl) {
      bulletsEl.innerHTML = '';
      (brief.bullets || []).forEach(b => {
        const li = document.createElement('li');
        li.innerHTML = b;   // may contain <b> tags
        bulletsEl.appendChild(li);
      });
      if (!(brief.bullets || []).length) {
        bulletsEl.innerHTML = '<li>Хорошего дня!</li>';
      }
    }

    // Stats chips
    const statsEl = section.querySelector('.today__brief-stats');
    if (statsEl) {
      const stats = brief.stats || {};
      const chips = [];
      if (stats.events_today > 0)
        chips.push(`<span class="today__brief-chip today__brief-chip--cal">🗓️ ${stats.events_today} встреч${_inflect(stats.events_today, 'а','и','')}</span>`);
      if (stats.urgent_count > 0)
        chips.push(`<span class="today__brief-chip today__brief-chip--urgent">📨 ${stats.urgent_count} срочных</span>`);
      if (stats.tasks_count > 0)
        chips.push(`<span class="today__brief-chip today__brief-chip--task">✅ ${stats.tasks_count} поручений</span>`);
      statsEl.innerHTML = chips.join('');
    }

    // Timestamp
    const tsEl = section.querySelector('.today__brief-ts');
    if (tsEl && brief.generated_at) {
      const d = new Date(brief.generated_at);
      const hh = String(d.getHours()).padStart(2, '0');
      const mm = String(d.getMinutes()).padStart(2, '0');
      tsEl.textContent = `Брифинг от ${hh}:${mm}`;
      if (brief.cached) tsEl.title = 'Кэш — нажмите ↻ для обновления';
    }
  }

  function _inflect(n, one, few, many) {
    const mod = n % 100;
    if (mod >= 11 && mod <= 19) return many;
    const r = n % 10;
    if (r === 1) return one;
    if (r >= 2 && r <= 4) return few;
    return many;
  }

  // Refresh brief button (GET with cache bypass)
  document.getElementById('today-brief-refresh')?.addEventListener('click', async () => {
    const btn = document.getElementById('today-brief-refresh');
    if (btn) { btn.disabled = true; btn.textContent = '⏳'; }
    await loadDailyBrief(true);
    if (btn) { btn.disabled = false; btn.textContent = '↻'; }
  });

  // Regenerate brief button (POST — full LLM re-generation, slower but fresh)
  document.getElementById('today-brief-regen')?.addEventListener('click', async () => {
    const btn = document.getElementById('today-brief-regen');
    if (btn) { btn.disabled = true; btn.title = 'Генерация…'; }
    try {
      await api.briefGenerate();
      await loadDailyBrief(false); // load the freshly generated brief
    } catch (err) {
      console.warn('briefGenerate failed:', err.message);
      // Fallback: try a refresh from cache
      await loadDailyBrief(true);
    } finally {
      if (btn) { btn.disabled = false; btn.title = 'Пересоздать брифинг с помощью ИИ (POST /brief/daily/generate)'; }
    }
  });

  // "Ask about my day" from brief section
  document.getElementById('today-brief-ask')?.addEventListener('click', () => {
    activateTab('chat');
    document.dispatchEvent(new CustomEvent('pa:chat-open', {
      detail: { message: '/summarize Дай сводку моего дня: встречи, срочные письма и ключевые задачи' },
    }));
  });

  // ── Upcoming meetings (Stage 5: Smart Meeting Prep) ───────────────────────────
  async function loadUpcomingMeetings() {
    const container = document.getElementById('today-meetings-section');
    if (!container) return;
    try {
      const res = await api.calendarUpcoming(7);
      renderUpcomingMeetings(res.events || [], container);
    } catch (_) {
      container.style.display = 'none';
    }
  }

  // Local-date helpers — keep the user's wall-clock in charge, not UTC.
  function _localDate(isoString) {
    if (!isoString) return null;
    const d = new Date(isoString);
    if (Number.isNaN(d.getTime())) return null;
    return d;
  }
  function _localDateKey(d) {
    if (!d) return '';
    // YYYY-MM-DD in the browser's local timezone
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, '0');
    const dd = String(d.getDate()).padStart(2, '0');
    return `${y}-${m}-${dd}`;
  }
  function _dayLabel(isoString) {
    const d = _localDate(isoString);
    if (!d) return '';
    const today = new Date();
    const todayKey = _localDateKey(today);
    const tomorrow = new Date(today);
    tomorrow.setDate(today.getDate() + 1);
    const tomorrowKey = _localDateKey(tomorrow);
    const dKey = _localDateKey(d);
    if (dKey === todayKey) return 'today';
    if (dKey === tomorrowKey) return 'tomorrow';
    return 'later';
  }
  function _dayHeader(group) {
    if (group === 'today')    return '🔆 Сегодня';
    if (group === 'tomorrow') return '📅 Завтра';
    return '🗓 Позже на этой неделе';
  }

  function renderUpcomingMeetings(events, container) {
    if (!events.length) { container.style.display = 'none'; return; }
    container.style.display = '';

    const countEl = container.querySelector('.today__meetings-count');
    if (countEl) countEl.textContent = String(events.length);

    const listEl = container.querySelector('.today__meetings-list');
    if (!listEl) return;
    listEl.innerHTML = '';

    // Group events into today / tomorrow / later. With many meetings on the
    // calendar (10+), today's events were getting buried in a flat list —
    // now they sit prominently at the top with their own header.
    const groups = { today: [], tomorrow: [], later: [] };
    events.forEach(ev => {
      const g = _dayLabel(ev.date);
      (groups[g] || groups.later).push(ev);
    });

    const order = ['today', 'tomorrow', 'later'];
    order.forEach(group => {
      const items = groups[group];
      if (!items.length) return;

      const header = document.createElement('div');
      header.className = `today__meetings-group-header today__meetings-group-header--${group}`;
      header.innerHTML =
        `<span class="today__meetings-group-title">${_dayHeader(group)}</span>` +
        `<span class="today__meetings-group-count">${items.length}</span>`;
      listEl.appendChild(header);

      items.forEach(ev => {
        const card = document.createElement('div');
        card.className = `today__meeting-card today__meeting-card--${group}`;

        const pCount = ev.participant_count || (ev.participants || []).length;
        const subParts = [
          pCount ? `${pCount} участн.` : '',
          ev.location ? `📍 ${ev.location}` : '',
        ].filter(Boolean);

        card.innerHTML =
          `<div class="today__meeting-header">` +
            `<div class="today__meeting-time">${_esc(ev.relative || ev.date?.slice(0,10) || '')}</div>` +
            `<div class="today__meeting-title">${_esc(ev.title)}</div>` +
          `</div>` +
          (subParts.length ? `<div class="today__meeting-sub">${_esc(subParts.join(' · '))}</div>` : '') +
          `<div class="today__meeting-actions">` +
            `<button class="today__meeting-prep-btn btn btn--xs btn--secondary" ` +
              `data-event-id="${_esc(ev.id)}" ` +
              `title="Собрать контекст и открыть чат для подготовки к встрече">` +
              `Подготовиться` +
            `</button>` +
          `</div>`;

        card.querySelector('.today__meeting-prep-btn')
          ?.addEventListener('click', async e => {
            e.stopPropagation();
            const btn = e.currentTarget;
            _openMeetingPrep(ev.id, ev.title, btn);
          });

        listEl.appendChild(card);
      });
    });
  }

  async function _openMeetingPrep(eventId, eventTitle, btn) {
    const origText = btn?.textContent;
    if (btn) { btn.disabled = true; btn.textContent = '⏳'; }
    try {
      const ctx = await api.calendarPrep(eventId);
      activateTab('chat');
      document.dispatchEvent(new CustomEvent('pa:chat-open', {
        detail: {
          message: ctx.context_prompt,
          mode: 'chat',
          meeting_context: ctx,
          title: `Подготовка: ${eventTitle}`,
        },
      }));
    } catch (_err) {
      // Graceful fallback — open chat without prep context
      activateTab('chat');
      document.dispatchEvent(new CustomEvent('pa:chat-open', {
        detail: { message: `Помоги подготовиться к встрече «${eventTitle}»`, mode: 'chat' },
      }));
      showToast('Контекст встречи недоступен — открыт чат', 'warn');
    } finally {
      if (btn) { btn.disabled = false; btn.textContent = origText; }
    }
  }

  // ── Quick Event Create (Stage 7: Calendar Intent NLP) ────────────────────────
  const quickCreateInput  = document.getElementById('today-quick-create-input');
  const quickCreateBtn    = document.getElementById('today-quick-create-btn');
  const quickPreviewCard  = document.getElementById('today-quick-preview-card');

  /** Show/hide the preview card with parsed draft data. */
  function _renderEventPreview(result) {
    if (!quickPreviewCard) return;
    if (!result || !result.draft) {
      quickPreviewCard.style.display = 'none';
      return;
    }
    const d = result.draft;
    const needsCalendar = result.needs_calendar;
    const availableCalendars = result.available_calendars || [];
    const calendarLabel = d.calendar_name || (needsCalendar ? 'не указан' : 'Work');

    const rows = [
      ['📅 Дата', d.date_iso || ''],
      ['⏰ Время', `${d.time_str || ''} (${d.duration_minutes || 60} мин)`],
      d.location ? ['📍 Место', d.location] : null,
      (d.participants || []).length ? ['👥 Участники', d.participants.join(', ')] : null,
      ['🗓 Календарь', calendarLabel],
    ].filter(Boolean);

    const warningsHtml = (d.warnings || []).length
      ? `<div class="today__qc-warnings">⚠️ ${d.warnings.join('; ')}</div>` : '';

    // Calendar selector dropdown when calendar is not detected
    let calendarSelectHtml = '';
    if (needsCalendar && availableCalendars.length) {
      const options = availableCalendars.map(c => `<option value="${_esc(c)}">${_esc(c)}</option>`).join('');
      calendarSelectHtml =
        `<div class="today__qc-calendar-select" style="margin:8px 0;">` +
          `<label style="font-size:12px;color:var(--color-text-muted);">Выберите календарь:</label>` +
          `<select id="today-qc-calendar" class="form-select" style="margin-left:6px;">` +
            `<option value="">-- выбрать --</option>` +
            options +
          `</select>` +
        `</div>`;
    }

    quickPreviewCard.innerHTML =
      `<div class="today__qc-title">${_esc(d.title)}</div>` +
      `<div class="today__qc-meta">` +
        rows.map(([label, val]) =>
          `<span class="today__qc-meta-row"><b>${label}</b> ${_esc(val)}</span>`
        ).join('') +
      `</div>` +
      calendarSelectHtml +
      warningsHtml +
      `<div class="today__qc-actions">` +
        `<button class="btn btn--xs btn--primary" id="today-qc-confirm">✓ Создать</button>` +
        `<button class="btn btn--xs btn--secondary" id="today-qc-edit">✏️ Изменить в чате</button>` +
        `<button class="btn btn--xs btn--ghost today__qc-cancel" id="today-qc-cancel">Отмена</button>` +
      `</div>`;
    quickPreviewCard.style.display = '';

    // Store the raw text for re-use
    quickPreviewCard._lastText = quickCreateInput?.value || '';

    document.getElementById('today-qc-confirm')?.addEventListener('click', async () => {
      const text = quickPreviewCard._lastText;
      if (!text) return;
      const btn = document.getElementById('today-qc-confirm');

      // Read selected calendar if dropdown exists
      const calSelect = document.getElementById('today-qc-calendar');
      const selectedCalendar = calSelect ? calSelect.value : '';
      if (needsCalendar && !selectedCalendar) {
        showToast('Выберите календарь перед созданием', 'warning');
        return;
      }

      if (btn) { btn.disabled = true; btn.textContent = '⏳'; }
      try {
        const res = await api.calendarCreateFromText(text, {
          confirmed: true,
          calendarName: selectedCalendar || undefined,
        });
        if (res.created) {
          showToast(`✅ Событие создано: ${res.draft?.title || ''}`, 'success');
          quickPreviewCard.style.display = 'none';
          if (quickCreateInput) quickCreateInput.value = '';
          // Refresh meetings list
          loadUpcomingMeetings();
          document.dispatchEvent(new Event('pa:vault-reloaded'));
        } else if (res.needs_calendar) {
          showToast('Выберите календарь перед созданием', 'warning');
        } else {
          showToast('Ошибка создания: ' + (res.error || 'неизвестная ошибка'), 'error');
        }
      } catch (err) {
        showToast('Ошибка: ' + err.message, 'error');
      } finally {
        if (btn) { btn.disabled = false; btn.textContent = '✓ Создать'; }
      }
    });

    document.getElementById('today-qc-edit')?.addEventListener('click', () => {
      // Open chat with /встреча command for the text
      activateTab('chat');
      document.dispatchEvent(new CustomEvent('pa:chat-open', {
        detail: { message: `/встреча ${quickPreviewCard._lastText || ''}` },
      }));
      quickPreviewCard.style.display = 'none';
      if (quickCreateInput) quickCreateInput.value = '';
    });

    document.getElementById('today-qc-cancel')?.addEventListener('click', () => {
      quickPreviewCard.style.display = 'none';
    });
  }

  /** Parse text and show preview card. */
  async function _quickParseIntent() {
    const text = quickCreateInput?.value?.trim();
    if (!text) return;
    if (quickCreateBtn) { quickCreateBtn.disabled = true; quickCreateBtn.textContent = '…'; }
    try {
      const result = await api.calendarParseIntent(text);
      _renderEventPreview(result);
    } catch (err) {
      showToast('Ошибка разбора: ' + err.message, 'error');
    } finally {
      if (quickCreateBtn) { quickCreateBtn.disabled = false; quickCreateBtn.textContent = '+'; }
    }
  }

  if (quickCreateBtn) {
    quickCreateBtn.addEventListener('click', _quickParseIntent);
  }
  if (quickCreateInput) {
    quickCreateInput.addEventListener('keydown', e => {
      if (e.key === 'Enter') { e.preventDefault(); _quickParseIntent(); }
      if (e.key === 'Escape') {
        if (quickPreviewCard) quickPreviewCard.style.display = 'none';
        quickCreateInput.value = '';
      }
    });
  }

  // ── /встреча slash command handler (called from chat.js) ─────────────────────
  document.addEventListener('pa:create-event', async e => {
    const text = e.detail?.text || '';
    if (!text) return;
    try {
      const result = await api.calendarParseIntent(text);
      if (!result?.draft) { showToast('Не удалось распознать событие', 'warn'); return; }
      // Show inline preview in today tab or in a modal-style overlay
      if (quickCreateInput) quickCreateInput.value = text;
      _renderEventPreview(result);
      activateTab('today');
    } catch (err) {
      showToast('Ошибка разбора события: ' + err.message, 'error');
    }
  });

  // ── Render ────────────────────────────────────────────────────────────────────
  function render(data) {
    // Greeting
    if (greetingEl) greetingEl.textContent = data.greeting || 'Добрый день';

    // Bullets
    if (bulletsEl) {
      bulletsEl.innerHTML = '';
      (data.bullets || []).forEach(text => {
        const li = document.createElement('li');
        li.innerHTML = text;   // may contain <b> tags
        bulletsEl.appendChild(li);
      });
    }

    // Meta
    if (updatedAtEl) updatedAtEl.textContent = data.updated_at || '–';
    if (nextUpdateEl) nextUpdateEl.textContent = data.next_update || '–';

    // Events
    renderEvents(data.events || [], data.events_total || 0);

    // Attention
    renderAttention(
      data.attention || [],
      data.attention_total || 0,
      data.urgent_count || 0,
    );

    // Suggestions
    renderSuggestions(data.suggestions || []);
  }

  // ── Events column ─────────────────────────────────────────────────────────────
  function renderEvents(events, total) {
    if (eventsCountEl) eventsCountEl.textContent = total || events.length || '0';
    if (!eventsListEl) return;
    eventsListEl.innerHTML = '';

    if (!events.length) {
      eventsListEl.innerHTML = '<div class="today__empty">Встреч сегодня нет</div>';
      return;
    }

    events.forEach(ev => {
      const el = document.createElement('div');
      el.className = 'today__event';
      el.setAttribute('role', 'button');
      el.setAttribute('tabindex', '0');

      const dotCls = ev.status === 'active' ? 'today__event-dot--active'
                   : ev.status === 'upcoming' ? 'today__event-dot--upcoming'
                   : 'today__event-dot--past';

      // Sub-line: attendees + location
      const subParts = [];
      if (ev.attendees && ev.attendees.length) subParts.push(ev.attendees.join(', '));
      if (ev.location) subParts.push(ev.location);
      if (ev.description) subParts.push(ev.description);

      // Chips
      const chips = [];
      if (ev.has_brief)  chips.push('<span class="today__chip today__chip--brief">🤖 бриф готов</span>');
      if (ev.is_urgent)  chips.push('<span class="today__chip today__chip--urgent">срочно</span>');
      if (ev.is_focus)   chips.push('<span class="today__chip today__chip--focus">🎯 фокус</span>');

      el.innerHTML =
        `<div class="today__event-time">${_esc(ev.time)}</div>` +
        `<div class="today__event-dot ${dotCls}"></div>` +
        `<div class="today__event-body">` +
          `<div class="today__event-title">${_esc(ev.title)}</div>` +
          (subParts.length ? `<div class="today__event-sub">${_esc(subParts.join(' · '))}</div>` : '') +
          (chips.length ? `<div class="today__event-chips">${chips.join('')}</div>` : '') +
        `</div>`;

      el.addEventListener('click', () => {
        if (ev.path) {
          activateTab('vault');
          document.dispatchEvent(new CustomEvent('vault:open', { detail: { path: ev.path } }));
        } else {
          activateTab('chat');
        }
      });

      eventsListEl.appendChild(el);
    });
  }

  // ── Attention column ──────────────────────────────────────────────────────────
  function renderAttention(items, total, urgentCount) {
    if (attentionCountEl) {
      attentionCountEl.textContent = urgentCount ? `${items.length} из ${total}` : String(items.length);
    }
    if (!attentionListEl) return;
    attentionListEl.innerHTML = '';

    if (!items.length) {
      attentionListEl.innerHTML = '<div class="today__empty">Срочных писем нет</div>';
      if (attentionFooter) attentionFooter.style.display = 'none';
      return;
    }

    items.forEach(item => {
      const el = document.createElement('div');
      el.className = 'today__attention';
      el.setAttribute('role', 'button');
      el.setAttribute('tabindex', '0');

      el.innerHTML =
        `<div class="today__avatar" style="background:${_esc(item.sender_color)}">` +
          `${_esc(item.sender_initials)}` +
        `</div>` +
        `<div class="today__attention-body">` +
          `<div class="today__attention-top">` +
            `<span class="today__attention-sender">${_esc(item.sender_name)}` +
              (item.sender_role ? ` <span style="font-weight:400;color:var(--color-text-faint)">· ${_esc(item.sender_role)}</span>` : '') +
            `</span>` +
            `<span class="today__attention-time">${_esc(item.time_label)}</span>` +
          `</div>` +
          `<div class="today__attention-subject">${_esc(item.subject)}</div>` +
          (item.preview ? `<div class="today__attention-preview">«${_esc(item.preview)}»</div>` : '') +
        `</div>`;

      el.addEventListener('click', () => {
        activateTab('inbox');
        document.dispatchEvent(new CustomEvent('inbox:open', { detail: { id: item.id } }));
      });

      attentionListEl.appendChild(el);
    });

    // Footer
    if (attentionFooter) {
      attentionFooter.style.display = 'flex';
      if (attentionTotal) {
        attentionTotal.textContent = `${urgentCount || items.length} из ${total} непрочитанных`;
      }
    }
  }

  // ── Suggestions column ─────────────────────────────────────────────────────────
  function renderSuggestions(suggestions) {
    if (suggestionsCount) suggestionsCount.textContent = String(suggestions.length);
    if (!suggestionsListEl) return;
    suggestionsListEl.innerHTML = '';

    if (!suggestions.length) {
      suggestionsListEl.innerHTML = '<div class="today__empty">Предложений нет</div>';
      return;
    }

    suggestions.forEach(s => {
      const el = document.createElement('div');
      el.className = 'today__suggestion';
      el.setAttribute('role', 'button');
      el.setAttribute('tabindex', '0');

      const icon = _SUGGESTION_ICONS[s.icon] || '✨';

      el.innerHTML =
        `<div class="today__suggestion-icon">${icon}</div>` +
        `<div class="today__suggestion-body">` +
          `<div class="today__suggestion-label">${_esc(s.label)}</div>` +
          `<div class="today__suggestion-detail">${_esc(s.detail)}</div>` +
        `</div>` +
        `<span class="today__suggestion-arrow">→</span>`;

      el.addEventListener('click', () => {
        activateTab('chat');
        document.dispatchEvent(new CustomEvent('pa:chat-open', {
          detail: {
            path: s.path,
            title: s.label,
            mode: s.action,
            message: s.message,
          },
        }));
      });

      suggestionsListEl.appendChild(el);
    });
  }

  // ── Buttons ───────────────────────────────────────────────────────────────────
  document.getElementById('today-ask-btn')?.addEventListener('click', () => {
    activateTab('chat');
    document.dispatchEvent(new CustomEvent('pa:chat-open', {
      detail: { message: 'Расскажи подробнее о моём расписании на сегодня' },
    }));
  });

  document.getElementById('today-calendar-btn')?.addEventListener('click', () => {
    activateTab('vault');
    document.dispatchEvent(new CustomEvent('vault:filter', { detail: { section: 'calendar' } }));
  });

  document.getElementById('today-summary-btn')?.addEventListener('click', () => {
    activateTab('chat');
    document.dispatchEvent(new CustomEvent('pa:chat-open', {
      detail: { message: '/summarize Суммаризируй мой день: встречи, важные письма и приоритеты' },
    }));
  });

  document.getElementById('today-events-nav')?.addEventListener('click', () => {
    activateTab('vault');
    document.dispatchEvent(new CustomEvent('vault:filter', { detail: { section: 'calendar' } }));
  });

  document.getElementById('today-attention-nav')?.addEventListener('click', () => {
    activateTab('inbox');
  });

  document.getElementById('today-open-inbox')?.addEventListener('click', () => {
    activateTab('inbox');
  });

  // ── Search redirect ────────────────────────────────────────────────────────────
  searchInput?.addEventListener('keydown', e => {
    if (e.key === 'Enter' && searchInput.value.trim()) {
      const q = searchInput.value.trim();
      searchInput.value = '';
      activateTab('search');
      const inp = document.getElementById('search-input');
      if (inp) {
        inp.value = q;
        inp.dispatchEvent(new Event('input', { bubbles: true }));
      }
    }
  });

  // ── Nav badge: urgent count ────────────────────────────────────────────────────
  function _updateBadge(data) {
    const badge = document.getElementById('nav-badge-today');
    if (!badge) return;
    const n = (data.urgent_count || 0) + (data.suggestions?.length || 0);
    if (n > 0) {
      badge.textContent = String(n);
      badge.style.display = '';
    } else {
      badge.style.display = 'none';
    }
  }

  // ── Activate: load on first visit, refresh on return ──────────────────────────
  let _loaded = false;

  document.querySelectorAll('.nav__item[data-tab="today"]').forEach(btn => {
    btn.addEventListener('click', async () => {
      if (!_loaded) {
        _loaded = true;
        await load();
      }
    });
  });

  // Also re-load after vault sync
  document.addEventListener('pa:vault-reloaded', async () => {
    _loaded = false;
    // Only reload if currently visible
    const panel = document.querySelector('.tab-panel[data-tab="today"]');
    if (panel && panel.classList.contains('tab-panel--active')) {
      _loaded = true;
      await load();
    }
  });

  // Pre-load if today is the default tab (hash)
  if (location.hash === '#today') {
    _loaded = true;
    load();
  }
}
