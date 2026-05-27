// =============================================================================
// rules.js — Rules tab: Матрица Эйзенхауэра · GTD-правила · Инструменты
// =============================================================================
import { api } from './api.js?v=20260520153419';

// ── Constants ─────────────────────────────────────────────────────────────────

const QUADRANT_META = {
  q1: { label: 'Q1 Срочно & Важно',        hint: '→ Сделать',       color: '#ef4444' },
  q2: { label: 'Q2 Важно, не срочно',      hint: '→ Запланировать', color: '#3b82f6' },
  q3: { label: 'Q3 Срочно, не важно',      hint: '→ Делегировать',  color: '#f97316' },
  q4: { label: 'Q4 Не срочно & не важно',  hint: '→ Устранить',     color: '#6b7280' },
};

const SOURCE_ICONS = {
  mail:     '✉️',
  calendar: '📅',
  project:  '🔢',
  contact:  '👤',
};

function _esc(s) {
  return String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function _srcIcon(src) { return SOURCE_ICONS[src] || '📌'; }

// ── Sub-tab switching ─────────────────────────────────────────────────────────
function initRuleTabs() {
  const tabs   = document.querySelectorAll('.rules__tab[data-rtab]');
  const panels = document.querySelectorAll('.rules__panel[id^="rtab-"]');

  tabs.forEach(btn => {
    btn.addEventListener('click', () => {
      tabs.forEach(t => t.classList.remove('rules__tab--active'));
      btn.classList.add('rules__tab--active');
      const target = btn.dataset.rtab;
      panels.forEach(p => {
        p.style.display = p.id === `rtab-${target}` ? '' : 'none';
      });
    });
  });
}

// ── Quadrant picker modal ─────────────────────────────────────────────────────
function showQuadrantPicker(onSelect) {
  const backdrop = document.createElement('div');
  backdrop.style.cssText = 'position:fixed;inset:0;z-index:500;background:rgba(0,0,0,.35);display:flex;align-items:center;justify-content:center';

  const box = document.createElement('div');
  box.style.cssText = 'background:var(--color-surface);border-radius:12px;padding:20px;min-width:320px;box-shadow:0 8px 32px rgba(0,0,0,.18)';
  box.innerHTML = '<div style="font-size:12px;font-weight:700;color:var(--color-text-muted);margin-bottom:14px;text-transform:uppercase;letter-spacing:.05em">Выберите квадрант</div>';

  const grid = document.createElement('div');
  grid.style.cssText = 'display:grid;grid-template-columns:1fr 1fr;gap:8px';
  Object.entries(QUADRANT_META).forEach(([key, m]) => {
    const btn = document.createElement('button');
    btn.style.cssText = `background:${m.color}22;color:${m.color};border:1px solid ${m.color}44;border-radius:8px;padding:12px 10px;font-size:12px;font-weight:600;cursor:pointer;text-align:left;line-height:1.3;transition:opacity .15s`;
    btn.textContent = m.label;
    btn.addEventListener('mouseenter', () => { btn.style.opacity = '.75'; });
    btn.addEventListener('mouseleave', () => { btn.style.opacity = '1'; });
    btn.addEventListener('click', () => { backdrop.remove(); onSelect(key); });
    grid.appendChild(btn);
  });

  const cancelBtn = document.createElement('button');
  cancelBtn.className = 'btn btn--secondary btn--sm';
  cancelBtn.style.cssText = 'width:100%;margin-top:12px;justify-content:center';
  cancelBtn.textContent = 'Отмена';
  cancelBtn.addEventListener('click', () => backdrop.remove());

  box.appendChild(grid);
  box.appendChild(cancelBtn);
  backdrop.appendChild(box);
  backdrop.addEventListener('click', e => { if (e.target === backdrop) backdrop.remove(); });
  document.body.appendChild(backdrop);
}

// =============================================================================
// EISENHOWER MATRIX
// =============================================================================

function initEisenhower(ctx) {
  const { showToast } = ctx;
  let eisTasks = [];

  async function loadEisenhower() {
    try {
      const data = await api.eisenhowerGet();
      eisTasks = data.tasks || [];
    } catch { eisTasks = []; }
    renderEisenhower();
  }

  function renderEisenhower() {
    ['q1','q2','q3','q4'].forEach(q => {
      const body = document.getElementById(`${q}-body`);
      if (!body) return;
      body.innerHTML = '';
      const tasks = eisTasks.filter(t => t.quadrant === q);

      if (!tasks.length) {
        body.innerHTML = '<div style="font-size:12px;color:var(--color-text-muted);padding:10px 0;font-style:italic">Нет задач</div>';
        return;
      }

      tasks.forEach(task => {
        const el = document.createElement('div');
        el.className = 'rules__task-item' + (task.done ? ' rules__task-item--done' : '');

        const srcIcon = task.source ? `${_srcIcon(task.source)}` : '';
        const srcText = [task.source_label, task.deadline].filter(Boolean).join(' · ');

        el.innerHTML = `
          <span class="rules__task-dot"></span>
          <div class="rules__task-body">
            <div class="rules__task-title">${_esc(task.title || '')}</div>
            ${(srcIcon || srcText) ? `<div class="rules__task-source">${srcIcon} ${_esc(srcText)}</div>` : ''}
          </div>
          <div class="rules__task-actions">
            <button class="rules__task-del" data-del="${task.id}" title="Удалить">✕</button>
          </div>
        `;

        // Click on task body → move to next quadrant
        el.querySelector('.rules__task-body').addEventListener('click', () => {
          const next = { q1:'q2', q2:'q3', q3:'q4', q4:'q1' }[q];
          const t = eisTasks.find(t => t.id === task.id);
          if (t) { t.quadrant = next; renderEisenhower(); }
        });

        el.querySelector('[data-del]')?.addEventListener('click', e => {
          e.stopPropagation();
          eisTasks = eisTasks.filter(t => t.id !== task.id);
          renderEisenhower();
        });

        body.appendChild(el);
      });
    });
  }

  // Inline "+ добавить" per quadrant
  document.querySelectorAll('.rules__inline-add[data-add-quadrant]').forEach(btn => {
    btn.addEventListener('click', () => {
      const q = btn.dataset.addQuadrant;
      const body = document.getElementById(`${q}-body`);
      if (!body) return;

      // Replace button with inline form
      const form = document.createElement('div');
      form.className = 'rules__inline-form';
      form.innerHTML = `
        <input type="text" placeholder="Название задачи…" autofocus>
        <button class="ok">OK</button>
        <button class="cancel">✕</button>
      `;
      btn.replaceWith(form);
      const inp = form.querySelector('input');
      inp.focus();

      const restore = () => { form.replaceWith(btn); };

      form.querySelector('.ok').addEventListener('click', () => {
        const title = inp.value.trim();
        if (title) {
          eisTasks.push({ id: Date.now().toString(), title, quadrant: q, done: false });
          renderEisenhower();
        }
        restore();
      });
      form.querySelector('.cancel').addEventListener('click', restore);
      inp.addEventListener('keydown', e => {
        if (e.key === 'Enter') { form.querySelector('.ok').click(); }
        if (e.key === 'Escape') restore();
      });
    });
  });

  // Global "+" button → picker then title input
  document.getElementById('eisenhower-add')?.addEventListener('click', () => {
    showQuadrantPicker(q => {
      const title = (window.prompt('Название задачи:') || '').trim();
      if (!title) return;
      eisTasks.push({ id: Date.now().toString(), title, quadrant: q, done: false });
      renderEisenhower();
    });
  });

  // AI redistribute button
  document.getElementById('eis-ai-btn')?.addEventListener('click', async () => {
    const btn = document.getElementById('eis-ai-btn');
    if (btn) { btn.disabled = true; btn.textContent = 'Обрабатываю…'; }
    try {
      // Ask AI to suggest reassignment via chat
      document.dispatchEvent(new CustomEvent('pa:chat-send', {
        detail: { mode: 'chat', message: `Перераспредели мои задачи по квадрантам Эйзенхауэра. Задачи: ${eisTasks.map(t => `"${t.title}" (${t.quadrant})`).join(', ')}. Ответь списком в формате: "задача → квадрант"`, suppressSend: false },
      }));
      // Switch to chat tab
      document.querySelector('.nav__item[data-tab="chat"]')?.click();
    } catch (err) {
      showToast('Ошибка: ' + err.message, 'error');
    } finally {
      if (btn) { btn.disabled = false; btn.innerHTML = '<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" width="14" height="14"><path stroke-linecap="round" stroke-linejoin="round" d="M9.813 15.904L9 18.75l-.813-2.846a4.5 4.5 0 00-3.09-3.09L2.25 12l2.846-.813a4.5 4.5 0 003.09-3.09L9 5.25l.813 2.846a4.5 4.5 0 003.09 3.09L15.75 12l-2.846.813a4.5 4.5 0 00-3.09 3.09z"/></svg> Перераспределить через AI'; }
    }
  });

  // Save button
  document.getElementById('eisenhower-save')?.addEventListener('click', async () => {
    try {
      await api.eisenhowerSave(eisTasks);
      const st = document.getElementById('eisenhower-status');
      if (st) { st.textContent = 'Сохранено ✓'; setTimeout(() => { st.textContent = ''; }, 2000); }
      showToast('Матрица сохранена', 'success');
    } catch (err) {
      showToast('Ошибка: ' + err.message, 'error');
    }
  });

  loadEisenhower();
}

// =============================================================================
// GTD RULES + STRUCTURED RULES
// =============================================================================

const QUADRANT_LABELS = Object.fromEntries(
  Object.entries(QUADRANT_META).map(([k, v]) => [k, v.label])
);

const ACTION_LABELS = {
  execute:  '⚡ Выполнить',
  schedule: '📅 Запланировать',
  delegate: '🤝 Делегировать',
  info:     '📌 Инфо',
  skip:     '🗑 Не делать',
};

function initGtdRules(ctx) {
  const { showToast } = ctx;
  const gtdList   = document.getElementById('gtd-rules-list');
  const gtdAddBtn = document.getElementById('gtd-add-rule');
  const saveAllBtn = document.getElementById('gtd-save-all-btn');
  const resetBtn   = document.getElementById('gtd-reset-btn');
  const statusEl   = document.getElementById('gtd-apply-status');
  let gtdRules = [];
  let gtdOriginal = '';

  function _dirty() {
    return JSON.stringify(gtdRules) !== gtdOriginal;
  }

  function _updateStatus(msg, ok) {
    if (!statusEl) return;
    statusEl.textContent = msg;
    statusEl.style.color = ok ? 'var(--primary)' : 'var(--color-text-muted)';
  }

  async function loadGtdRules() {
    try {
      const data = await api.gtdRulesGet();
      gtdRules = data.rules || [];
      gtdOriginal = JSON.stringify(gtdRules);
    } catch { gtdRules = []; }
    renderGtdRules();
    updateGtdCount();
  }

  function updateGtdCount() {
    const el = document.getElementById('rules-gtd-count');
    if (el) el.textContent = gtdRules.length;
  }

  function renderGtdRules() {
    if (!gtdList) return;
    gtdList.innerHTML = '';
    if (!gtdRules.length) {
      gtdList.innerHTML = '<div style="font-size:12px;color:var(--color-text-muted);padding:8px 0">Нет правил. Нажмите «Добавить правило».</div>';
      return;
    }
    gtdRules.forEach((rule, idx) => {
      const row = document.createElement('div');
      row.style.cssText = 'display:flex;align-items:center;gap:8px;padding:7px 0;border-bottom:1px solid var(--color-border)';
      const qLabel = QUADRANT_LABELS[rule.quadrant] || '—';
      row.innerHTML = `
        <input class="settings__input" style="flex:1;max-width:170px"
          value="${_esc(rule.keyword || '')}" placeholder="Ключевое слово"
          data-field="keyword" data-idx="${idx}">
        <span style="font-size:12px;color:var(--color-text-muted)">→</span>
        <input class="settings__input" style="flex:2"
          value="${_esc(rule.action || '')}" placeholder="inbox, next, someday…"
          data-field="action" data-idx="${idx}">
        <button class="btn btn--sm btn--secondary" data-pick-quadrant="${idx}"
          style="flex:0;white-space:nowrap;min-width:120px;font-size:11px">${_esc(qLabel)}</button>
        <button class="btn btn--sm btn--secondary" data-delete="${idx}" style="flex:0;color:#ef4444;border-color:#ef4444">✕</button>
      `;
      row.querySelectorAll('[data-field]').forEach(el => {
        el.addEventListener('input', () => {
          gtdRules[parseInt(el.dataset.idx)][el.dataset.field] = el.value;
        });
      });
      row.querySelector(`[data-pick-quadrant="${idx}"]`)?.addEventListener('click', () => {
        showQuadrantPicker(key => { gtdRules[idx].quadrant = key; renderGtdRules(); });
      });
      row.querySelector('[data-delete]')?.addEventListener('click', () => {
        gtdRules.splice(idx, 1);
        renderGtdRules();
        updateGtdCount();
      });
      gtdList.appendChild(row);
    });
  }

  gtdAddBtn?.addEventListener('click', () => {
    gtdRules.push({ id: Date.now().toString(), keyword: '', action: 'inbox', quadrant: 'q2' });
    renderGtdRules();
    updateGtdCount();
  });

  saveAllBtn?.addEventListener('click', async () => {
    try {
      await api.gtdRulesSave(gtdRules);
      gtdOriginal = JSON.stringify(gtdRules);
      _updateStatus('Применено ✓', true);
      setTimeout(() => _updateStatus('', false), 2500);
      showToast('GTD-правила применены', 'success');
    } catch (err) { showToast('Ошибка: ' + err.message, 'error'); }
  });

  resetBtn?.addEventListener('click', async () => {
    if (!confirm('Сбросить все GTD-правила и структурированные правила к последнему сохранённому состоянию?')) return;
    await loadGtdRules();
    _updateStatus('Сброшено', false);
    setTimeout(() => _updateStatus('', false), 2000);
  });

  loadGtdRules();
}

// ── Structured Rules ──────────────────────────────────────────────────────────
function initStructuredRules(ctx) {
  const { showToast } = ctx;
  const container = document.getElementById('structured-rules-container');
  if (!container) return;
  let rules = [];

  async function loadRules() {
    try { const data = await api.rulesList(); rules = data.rules || []; }
    catch { rules = []; }
    renderRules();
  }

  function renderRules() {
    container.innerHTML = '';

    const header = document.createElement('div');
    header.style.cssText = 'display:flex;gap:8px;padding:6px 0;border-bottom:2px solid var(--color-border);font-size:10px;font-weight:700;color:var(--color-text-muted);text-transform:uppercase;letter-spacing:.04em';
    header.innerHTML = `
      <div style="flex:2">Название</div><div style="flex:3">Ключевые слова</div>
      <div style="flex:3">Контакты</div><div style="flex:2">Квадрант</div>
      <div style="flex:2">Действие</div><div style="flex:0 0 70px">Приор.</div>
      <div style="flex:0 0 60px;text-align:center">Вкл</div><div style="flex:0 0 28px"></div>
    `;
    container.appendChild(header);

    if (!rules.length) {
      const empty = document.createElement('div');
      empty.style.cssText = 'font-size:12px;color:var(--color-text-muted);padding:10px 0';
      empty.textContent = 'Нет правил. Нажмите «+ Правило».';
      container.appendChild(empty);
      return;
    }

    rules.forEach((rule, idx) => {
      const row = document.createElement('div');
      row.style.cssText = 'display:flex;align-items:center;gap:8px;padding:6px 0;border-bottom:1px solid var(--color-border)';
      const qLabel = QUADRANT_LABELS[rule.eisenhower_quadrant] || '—';
      const aLabel = ACTION_LABELS[rule.action_type] || '—';
      row.innerHTML = `
        <input class="settings__input" style="flex:2" placeholder="Название"
          value="${_esc(rule.name||'')}" data-field="name" data-idx="${idx}">
        <input class="settings__input" style="flex:3" placeholder="слово1, слово2"
          value="${_esc((rule.keywords||[]).join(', '))}" data-field="keywords" data-idx="${idx}">
        <input class="settings__input" style="flex:3" placeholder="email1, email2"
          value="${_esc((rule.contacts||[]).join(', '))}" data-field="contacts" data-idx="${idx}">
        <button class="btn btn--sm btn--secondary" style="flex:2;font-size:11px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap"
          data-pick-quadrant="${idx}" title="${_esc(qLabel)}">${_esc(qLabel)}</button>
        <select class="settings__input" style="flex:2;font-size:11px" data-field="action_type" data-idx="${idx}">
          ${Object.entries(ACTION_LABELS).map(([k,v])=>`<option value="${k}"${rule.action_type===k?' selected':''}>${v}</option>`).join('')}
        </select>
        <input type="number" class="settings__input" style="flex:0 0 70px" min="1" max="999"
          value="${rule.priority||100}" data-field="priority" data-idx="${idx}">
        <div style="flex:0 0 60px;display:flex;justify-content:center">
          <input type="checkbox" ${rule.enabled!==false?'checked':''} data-field="enabled" data-idx="${idx}"
            style="accent-color:var(--primary);width:15px;height:15px;cursor:pointer">
        </div>
        <button class="btn btn--sm" style="flex:0 0 28px;background:#fee2e2;color:#ef4444;border:none;padding:0;width:28px;justify-content:center"
          data-delete="${idx}">✕</button>
      `;
      row.querySelectorAll('[data-field]').forEach(el => {
        const sync = () => {
          const i = parseInt(el.dataset.idx);
          const f = el.dataset.field;
          if (el.type==='checkbox') rules[i][f]=el.checked;
          else if (f==='keywords'||f==='contacts') rules[i][f]=el.value.split(',').map(s=>s.trim()).filter(Boolean);
          else if (f==='priority') rules[i][f]=parseInt(el.value)||100;
          else rules[i][f]=el.value;
        };
        el.addEventListener('change',sync); el.addEventListener('input',sync);
      });
      row.querySelector(`[data-pick-quadrant="${idx}"]`)?.addEventListener('click', () => {
        showQuadrantPicker(key => { rules[idx].eisenhower_quadrant=key; renderRules(); });
      });
      row.querySelector('[data-delete]')?.addEventListener('click', async () => {
        if (rule.id) { try { await api.rulesDelete(rule.id); } catch {} }
        rules.splice(idx,1); renderRules();
      });
      container.appendChild(row);
    });
  }

  document.getElementById('structured-rules-add')?.addEventListener('click', () => {
    rules.push({ name:'', keywords:[], contacts:[], eisenhower_quadrant:'q2', action_type:'info', priority:100, enabled:true });
    renderRules();
  });

  // Save is wired to the main "Применить" button in GTD tab
  document.getElementById('gtd-save-all-btn')?.addEventListener('click', async () => {
    for (const rule of rules) {
      try {
        if (rule.id) await api.rulesUpdate(rule.id, rule);
        else { const c = await api.rulesCreate(rule); rule.id = c.id; }
      } catch (err) { console.error('[rules] save error:', err); }
    }
    await loadRules();
  }, { capture: false });

  loadRules();
}

// =============================================================================
// ИНСТРУМЕНТЫ tab — classify, tool prompts, tools registry
// Wiring is already done in settings.js; here we just trigger load on tab open
// =============================================================================

function initToolsTab(ctx) {
  const { showToast } = ctx;
  let toolsLoaded = false;
  let classifyLoaded = false;

  // Count badge: total enabled tools
  async function updateToolsCount() {
    try {
      const data = await api.toolsList();
      const tools = data.tools || data || [];
      const count = Array.isArray(tools) ? tools.filter(t => t.enabled !== false).length : 0;
      const el = document.getElementById('rules-tools-count');
      if (el) el.textContent = count || tools.length || 0;
    } catch {}
  }

  // When user clicks the "Инструменты" sub-tab, load data
  document.querySelector('.rules__tab[data-rtab="tools"]')?.addEventListener('click', () => {
    if (!classifyLoaded) {
      api.classifyConfig().then(data => {
        const el = document.getElementById('classify-editor');
        if (el) el.value = data.yaml_text || '';
        classifyLoaded = true;
      }).catch(() => {});
    }
    if (!toolsLoaded) {
      loadToolsList();
      loadToolPrompts();
      toolsLoaded = true;
    }
  });

  updateToolsCount();

  // ── Classify ────────────────────────────────────────────────────────────
  const classifyEditor   = document.getElementById('classify-editor');
  const classifySaveBtn  = document.getElementById('classify-save-btn');
  const classifyApplyBtn = document.getElementById('classify-apply-btn');
  const classifyResetBtn = document.getElementById('classify-reset-btn');
  const classifyStatus   = document.getElementById('classify-status');

  function _setClassifyStatus(msg, ok) {
    if (!classifyStatus) return;
    classifyStatus.textContent = msg;
    classifyStatus.style.color = ok ? 'var(--primary)' : '#ef4444';
  }

  classifySaveBtn?.addEventListener('click', async () => {
    try {
      await api.classifySave(classifyEditor?.value || '');
      _setClassifyStatus('Сохранено ✓', true);
      setTimeout(() => _setClassifyStatus('', true), 2000);
      showToast('classify.yaml сохранён', 'success');
    } catch (err) { showToast('Ошибка: ' + err.message, 'error'); }
  });

  classifyApplyBtn?.addEventListener('click', async () => {
    classifyApplyBtn.disabled = true;
    _setClassifyStatus('Применяю…', true);
    try {
      await api.classifyApply();
      _setClassifyStatus('Применено ✓', true);
      setTimeout(() => _setClassifyStatus('', true), 2500);
      showToast('Классификация применена к vault', 'success');
      document.dispatchEvent(new CustomEvent('pa:tags-reset'));
    } catch (err) {
      _setClassifyStatus('Ошибка', false);
      showToast('Ошибка: ' + err.message, 'error');
    } finally { classifyApplyBtn.disabled = false; }
  });

  classifyResetBtn?.addEventListener('click', async () => {
    if (!confirm('Сбросить все классификационные теги в vault?')) return;
    try {
      await api.classifyResetTags();
      _setClassifyStatus('Сброшено ✓', true);
      setTimeout(() => _setClassifyStatus('', true), 2000);
      showToast('Теги сброшены', 'success');
      document.dispatchEvent(new CustomEvent('pa:tags-reset'));
    } catch (err) { showToast('Ошибка: ' + err.message, 'error'); }
  });

  // ── Stage 8: LLM batch classify ─────────────────────────────────────────
  const llmBatchBtn    = document.getElementById('classify-llm-batch-btn');
  const llmStatsEl     = document.getElementById('classify-llm-stats');

  async function _loadLLMStats() {
    if (!llmStatsEl) return;
    try {
      const s = await api.classifyStats();
      if (s.status === 'ok') {
        const cats = Object.entries(s.category_distribution || {})
          .sort((a, b) => b[1] - a[1])
          .slice(0, 5)
          .map(([k, v]) => `${k}: ${v}`)
          .join(', ');
        llmStatsEl.innerHTML =
          `<span class="classify-stat"><b>📄 ${s.total_docs}</b> документов</span>` +
          `<span class="classify-stat"><b>🤖 ${s.ai_classified}</b> AI-классифицировано</span>` +
          `<span class="classify-stat"><b>💾 ${s.cache?.total_entries ?? 0}</b> в кэше</span>` +
          (cats ? `<span class="classify-stat-cats">${cats}</span>` : '');
      }
    } catch { /* stats are optional */ }
  }

  llmBatchBtn?.addEventListener('click', async () => {
    llmBatchBtn.disabled = true;
    _setClassifyStatus('Запуск ИИ-классификации…', true);
    try {
      const res = await api.classifyLLMBatch();
      if (res.status === 'started') {
        _setClassifyStatus(`🤖 Запущено в фоне (порог: ${res.threshold})`, true);
        showToast('LLM классификация запущена', 'success');
        // Refresh stats after a short delay
        setTimeout(_loadLLMStats, 3000);
      } else if (res.status === 'disabled') {
        _setClassifyStatus('LLM классификация отключена (enabled: false)', false);
        showToast('Включите llm_classify.enabled в classify.yaml', 'warning');
      } else {
        _setClassifyStatus(res.message || 'Ошибка', false);
      }
    } catch (err) {
      _setClassifyStatus('Ошибка: ' + err.message, false);
      showToast('Ошибка LLM классификации', 'error');
    } finally {
      llmBatchBtn.disabled = false;
    }
  });

  // Load stats when classify sub-tab becomes active
  document.addEventListener('pa:classify-tab-open', _loadLLMStats);

  // ── Tool prompts ────────────────────────────────────────────────────────
  const tpDraft    = document.getElementById('tp-draft');
  const tpSummarize= document.getElementById('tp-summarize');
  const tpSaveBtn  = document.getElementById('tp-save-btn');
  const tpDraftReset   = document.getElementById('tp-draft-reset');
  const tpSumReset     = document.getElementById('tp-summarize-reset');
  const tpStatus       = document.getElementById('tp-status');
  const tpDraftLen     = document.getElementById('tp-draft-len');
  const tpSumLen       = document.getElementById('tp-summarize-len');
  const tpDraftHint    = document.getElementById('tp-draft-hint');
  const tpSumHint      = document.getElementById('tp-summarize-hint');
  const tpFilePath     = document.getElementById('tp-file-path');

  // Delegate prompt + contacts editor (Rules → Инструменты)
  const tpDelegate     = document.getElementById('tp-delegate');
  const tpDelegateReset= document.getElementById('tp-delegate-reset');
  const tpDelegateLen  = document.getElementById('tp-delegate-len');
  const tpDelegateHint = document.getElementById('tp-delegate-hint');
  const dlgList        = document.getElementById('dlg-contacts-list');
  const dlgAddBtn      = document.getElementById('dlg-contacts-add');
  let _dlgContacts     = [];   // local working copy
  let tpDefaultDelegate = '';

  function _lenSync(ta, lenEl) {
    if (!ta || !lenEl) return;
    ta.addEventListener('input', () => { lenEl.textContent = ta.value.length; });
  }
  _lenSync(tpDraft, tpDraftLen);
  _lenSync(tpSummarize, tpSumLen);
  _lenSync(tpDelegate, tpDelegateLen);

  // Cache defaults so save logic can collapse "default content" -> empty
  // override (which falls back to default on next read).
  let tpDefaultDraft = '';
  let tpDefaultSum   = '';

  function _updateHint(hintEl, textarea, defaultText) {
    if (!hintEl || !textarea) return;
    const isDefault = (textarea.value || '').trim() === (defaultText || '').trim();
    hintEl.textContent = isDefault ? 'Дефолтный промпт' : 'Кастомный промпт';
    hintEl.style.color = isDefault ? 'var(--text-secondary)' : 'var(--primary)';
  }

  // Render the editable contacts list.  Each row is a 4-input strip
  // (name / email / role / note) plus a delete button.  Changes mutate the
  // local ``_dlgContacts`` buffer; persistence happens on "Сохранить промпты".
  function _renderDelegateContacts() {
    if (!dlgList) return;
    dlgList.innerHTML = '';
    if (!_dlgContacts.length) {
      dlgList.innerHTML =
        '<div style="font-size:12px;color:var(--color-text-muted);padding:6px 0">' +
        'Пока никого. Нажми «+ Добавить сотрудника».</div>';
      return;
    }
    _dlgContacts.forEach((c, idx) => {
      const row = document.createElement('div');
      row.style.cssText =
        'display:grid;grid-template-columns:1.2fr 1.4fr 1fr 1.4fr auto;' +
        'gap:6px;align-items:center;padding:4px 0;';
      row.innerHTML = `
        <input class="settings__input" placeholder="ФИО"     value="${_esc(c.name  || '')}" data-f="name"  data-i="${idx}">
        <input class="settings__input" placeholder="Email *" value="${_esc(c.email || '')}" data-f="email" data-i="${idx}" type="email">
        <input class="settings__input" placeholder="Роль"    value="${_esc(c.role  || '')}" data-f="role"  data-i="${idx}">
        <input class="settings__input" placeholder="Заметка" value="${_esc(c.note  || '')}" data-f="note"  data-i="${idx}">
        <button class="btn btn--sm btn--secondary" data-rm="${idx}"
                style="color:#ef4444;border-color:#ef4444;padding:4px 10px">✕</button>`;
      row.querySelectorAll('input[data-f]').forEach(el => {
        el.addEventListener('input', () => {
          _dlgContacts[parseInt(el.dataset.i, 10)][el.dataset.f] = el.value;
        });
      });
      row.querySelector('[data-rm]')?.addEventListener('click', () => {
        _dlgContacts.splice(idx, 1);
        _renderDelegateContacts();
      });
      dlgList.appendChild(row);
    });
  }

  function _esc(s) {
    return String(s || '')
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  dlgAddBtn?.addEventListener('click', () => {
    _dlgContacts.push({ name: '', email: '', role: '', note: '' });
    _renderDelegateContacts();
    // Focus the email cell of the new row so the user can start typing
    const last = dlgList?.querySelector('div:last-child input[data-f="email"]');
    last?.focus();
  });

  async function loadToolPrompts() {
    try {
      const data = await api.toolPromptsGet();
      tpDefaultDraft = data.default_draft_system || '';
      tpDefaultSum   = data.default_summarize_system || '';
      tpDefaultDelegate = data.default_delegate_system || '';
      if (tpDraft) {
        // Show effective text (user override OR default). ``effective_*`` is
        // the dedicated UI field; fall back to ``*_system || default`` for
        // older backends.
        tpDraft.value = data.effective_draft_system || data.draft_system || tpDefaultDraft;
        if (tpDraftLen) tpDraftLen.textContent = tpDraft.value.length;
      }
      if (tpSummarize) {
        tpSummarize.value = data.effective_summarize_system || data.summarize_system || tpDefaultSum;
        if (tpSumLen) tpSumLen.textContent = tpSummarize.value.length;
      }
      if (tpDelegate) {
        tpDelegate.value = data.effective_delegate_system || data.delegate_system || tpDefaultDelegate;
        if (tpDelegateLen) tpDelegateLen.textContent = tpDelegate.value.length;
      }
      _dlgContacts = Array.isArray(data.delegate_contacts) ? data.delegate_contacts.slice() : [];
      _renderDelegateContacts();
      _updateHint(tpDraftHint, tpDraft, tpDefaultDraft);
      _updateHint(tpSumHint,   tpSummarize, tpDefaultSum);
      _updateHint(tpDelegateHint, tpDelegate, tpDefaultDelegate);
      if (tpFilePath) tpFilePath.textContent = data.file_path || data.prompts_file_path || '—';
    } catch {}
  }

  // Live-update hint as user types so the badge flips от "Дефолтный" к
  // "Кастомный" в момент любого изменения относительно дефолта.
  tpDraft?.addEventListener('input',     () => _updateHint(tpDraftHint, tpDraft, tpDefaultDraft));
  tpSummarize?.addEventListener('input', () => _updateHint(tpSumHint,   tpSummarize, tpDefaultSum));
  tpDelegate?.addEventListener('input',  () => _updateHint(tpDelegateHint, tpDelegate, tpDefaultDelegate));

  tpSaveBtn?.addEventListener('click', async () => {
    try {
      // If the textarea content equals the default verbatim, save empty
      // string — that way effective behaviour stays "use default" and the
      // tool_prompts.json doesn't pin a frozen copy that would drift from
      // future default updates.
      const draftBody    = (tpDraft?.value    || '').trim();
      const sumBody      = (tpSummarize?.value|| '').trim();
      const delegateBody = (tpDelegate?.value || '').trim();
      // Filter contacts client-side to mirror server validation (need email).
      const contacts = _dlgContacts
        .map(c => ({
          name:  (c.name  || '').trim(),
          email: (c.email || '').trim(),
          role:  (c.role  || '').trim(),
          note:  (c.note  || '').trim(),
        }))
        .filter(c => c.email.includes('@'));
      await api.toolPromptsSave({
        draft_system:      draftBody    === tpDefaultDraft.trim()    ? '' : draftBody,
        summarize_system:  sumBody      === tpDefaultSum.trim()      ? '' : sumBody,
        delegate_system:   delegateBody === tpDefaultDelegate.trim() ? '' : delegateBody,
        delegate_contacts: contacts,
      });
      if (tpStatus) { tpStatus.textContent = 'Сохранено ✓'; setTimeout(() => { tpStatus.textContent = ''; }, 2000); }
      showToast('Промпты сохранены', 'success');
      await loadToolPrompts();  // refresh hints after save
    } catch (err) { showToast('Ошибка: ' + err.message, 'error'); }
  });

  tpDraftReset?.addEventListener('click', async () => {
    try {
      await api.toolPromptsSave({
        draft_system: '',
        summarize_system: (tpSummarize?.value||'').trim(),
        delegate_system:  (tpDelegate?.value ||'').trim(),
        delegate_contacts: _dlgContacts.filter(c => (c.email||'').includes('@')),
      });
      await loadToolPrompts();
      showToast('Промпт черновика сброшен к дефолту', 'success');
    } catch (err) { showToast('Ошибка: ' + err.message, 'error'); }
  });

  tpSumReset?.addEventListener('click', async () => {
    try {
      await api.toolPromptsSave({
        draft_system: (tpDraft?.value||'').trim(),
        summarize_system: '',
        delegate_system:  (tpDelegate?.value ||'').trim(),
        delegate_contacts: _dlgContacts.filter(c => (c.email||'').includes('@')),
      });
      await loadToolPrompts();
      showToast('Промпт суммаризации сброшен к дефолту', 'success');
    } catch (err) { showToast('Ошибка: ' + err.message, 'error'); }
  });

  tpDelegateReset?.addEventListener('click', async () => {
    try {
      await api.toolPromptsSave({
        draft_system:    (tpDraft?.value     ||'').trim(),
        summarize_system:(tpSummarize?.value ||'').trim(),
        delegate_system: '',
        delegate_contacts: _dlgContacts.filter(c => (c.email||'').includes('@')),
      });
      await loadToolPrompts();
      showToast('Промпт делегирования сброшен к дефолту', 'success');
    } catch (err) { showToast('Ошибка: ' + err.message, 'error'); }
  });

  // ── Tools registry ──────────────────────────────────────────────────────
  const toolsList    = document.getElementById('tools-list');
  const toolsReload  = document.getElementById('tools-reload-btn');

  async function loadToolsList() {
    if (!toolsList) return;
    toolsList.innerHTML = '<div style="font-size:12px;color:var(--color-text-muted)">Загрузка…</div>';
    try {
      const data = await api.toolsList();
      const tools = data.tools || (Array.isArray(data) ? data : []);
      toolsList.innerHTML = '';
      if (!tools.length) { toolsList.innerHTML = '<div style="font-size:12px;color:var(--color-text-muted)">Нет зарегистрированных инструментов</div>'; return; }
      tools.forEach(t => {
        const row = document.createElement('div');
        row.style.cssText = 'display:flex;align-items:center;gap:10px;padding:8px 0;border-bottom:1px solid var(--color-border)';
        row.innerHTML = `
          <label style="display:flex;align-items:center;gap:8px;flex:1;cursor:pointer;font-size:13px">
            <input type="checkbox" ${t.enabled!==false?'checked':''} data-tool-id="${_esc(t.id||t.name)}"
              style="accent-color:var(--primary);width:15px;height:15px">
            <span style="font-weight:500">${_esc(t.name||t.id)}</span>
          </label>
          <span style="font-size:11px;color:var(--color-text-muted);flex:2">${_esc(t.description||'')}</span>
        `;
        row.querySelector('input')?.addEventListener('change', async e => {
          try { await api.toolToggle(t.id||t.name, e.target.checked); updateToolsCount(); }
          catch (err) { showToast('Ошибка: ' + err.message, 'error'); e.target.checked = !e.target.checked; }
        });
        toolsList.appendChild(row);
      });
      updateToolsCount();
    } catch (err) {
      toolsList.innerHTML = `<div style="font-size:12px;color:#ef4444">Ошибка: ${_esc(err.message)}</div>`;
    }
  }

  toolsReload?.addEventListener('click', () => { toolsLoaded = false; loadToolsList(); });
}

// =============================================================================
// AI tool settings sub-tab — config.json-backed, auto-generated from schema
// =============================================================================
//
// The form is built dynamically from the schema returned by
// GET /api/v1/rules/settings, so adding a setting in EDITABLE_FIELDS (backend)
// automatically surfaces a field here — no UI edit required.
//
// data-testid convention (for Playwright E2E): inputs => "set-<field>",
// the save-all button => "save-rules".

const _AI_GROUP_LABELS = {
  mlx:      'MLX — локальная модель',
  mail:     'Почта',
  calendar: 'Календарь',
  tests:    'Тестирование',
  other:    'Прочее',
};

function initAiSettings(ctx) {
  const showToast = (ctx && ctx.showToast) ? ctx.showToast : () => {};
  const formEl   = document.getElementById('ai-settings-form');
  const saveBtn  = document.getElementById('ai-settings-save');
  const reloadBtn = document.getElementById('ai-settings-reload');
  const statusEl = document.getElementById('ai-settings-status');
  const pathEl   = document.getElementById('ai-settings-path');
  if (!formEl) return;

  let schema = {};
  let loaded = false;

  function _status(msg, ok = true) {
    if (!statusEl) return;
    statusEl.textContent = msg;
    statusEl.style.color = ok ? 'var(--color-text-muted)' : '#ef4444';
    if (ok && msg) {
      setTimeout(() => { if (statusEl.textContent === msg) statusEl.textContent = ''; }, 2500);
    }
  }

  function _clientValidate(name, value) {
    const sp = schema[name] || {};
    if (sp.type === 'int' || sp.type === 'float') {
      const num = Number(value);
      if (value === '' || Number.isNaN(num)) return `${sp.label || name}: введите число`;
      if (sp.min != null && num < sp.min) return `${sp.label || name}: минимум ${sp.min}`;
      if (sp.max != null && num > sp.max) return `${sp.label || name}: максимум ${sp.max}`;
    }
    return null;
  }

  function _readField(name) {
    const el = formEl.querySelector(`[data-field="${name}"]`);
    if (!el) return undefined;
    const sp = schema[name] || {};
    if (sp.type === 'bool')  return el.checked;
    if (sp.type === 'int')   return parseInt(el.value, 10);
    if (sp.type === 'float') return parseFloat(el.value);
    return el.value;
  }

  async function _saveOne(name) {
    const value = _readField(name);
    const err = _clientValidate(name, value);
    if (err) { _status(err, false); return; }
    try {
      await api.rulesSettingsSave({ [name]: value });
      _status('Сохранено ✓');
    } catch (e) {
      _status('Ошибка: ' + e.message, false);
    }
  }

  function _inputHtml(name, value) {
    const sp = schema[name] || {};
    const testid = 'set-' + name;
    const base = 'font-size:13px;padding:5px 8px;border:1px solid var(--color-border);border-radius:6px;background:var(--color-surface);color:var(--color-text)';
    if (sp.type === 'bool') {
      return `<input type="checkbox" data-field="${name}" data-testid="${testid}" ${value ? 'checked' : ''} style="width:16px;height:16px;accent-color:var(--primary)">`;
    }
    if (sp.type === 'text') {
      return `<textarea data-field="${name}" data-testid="${testid}" rows="3" style="${base};width:100%;resize:vertical">${_esc(value ?? '')}</textarea>`;
    }
    if (sp.type === 'int' || sp.type === 'float') {
      const step = sp.type === 'float' ? '0.05' : '1';
      const min = sp.min != null ? `min="${sp.min}"` : '';
      const max = sp.max != null ? `max="${sp.max}"` : '';
      return `<input type="number" step="${step}" ${min} ${max} data-field="${name}" data-testid="${testid}" value="${_esc(String(value ?? ''))}" style="${base};width:120px;text-align:right">`;
    }
    return `<input type="text" data-field="${name}" data-testid="${testid}" value="${_esc(value ?? '')}" style="${base};width:260px">`;
  }

  function _fieldRow(name, value) {
    const sp = schema[name] || {};
    const help = sp.help
      ? `<span title="${_esc(sp.help)}" style="cursor:help;display:inline-flex;align-items:center;justify-content:center;width:15px;height:15px;border-radius:50%;background:var(--color-border);color:var(--color-text-muted);font-size:10px;font-weight:700">?</span>`
      : '';
    const row = document.createElement('div');
    row.style.cssText = 'display:flex;align-items:center;gap:10px;padding:9px 0;border-bottom:1px solid var(--color-border)';
    row.innerHTML = `
      <label style="flex:1;font-size:13px;display:flex;align-items:center;gap:6px">
        <span>${_esc(sp.label || name)}</span>${help}
      </label>
      <div style="flex:0 0 auto;display:flex;justify-content:flex-end;align-items:center">${_inputHtml(name, value)}</div>`;
    return row;
  }

  async function load() {
    formEl.innerHTML = '<div style="font-size:12px;color:var(--color-text-muted)">Загрузка…</div>';
    try {
      const data = await api.rulesSettingsGet();
      schema = data.schema || {};
      const values = data.settings || {};
      if (pathEl && data.config_path) pathEl.textContent = data.config_path;

      const byGroup = {};
      Object.keys(schema).forEach(name => {
        const g = schema[name].group || 'other';
        (byGroup[g] = byGroup[g] || []).push(name);
      });

      formEl.innerHTML = '';
      Object.keys(byGroup).forEach(g => {
        const header = document.createElement('div');
        header.style.cssText = 'font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.05em;color:var(--color-text-muted);margin:16px 0 2px';
        header.textContent = _AI_GROUP_LABELS[g] || g;
        formEl.appendChild(header);
        byGroup[g].forEach(name => {
          const row = _fieldRow(name, values[name]);
          formEl.appendChild(row);
          row.querySelector('[data-field]')?.addEventListener('change', () => _saveOne(name));
        });
      });
      loaded = true;
    } catch (e) {
      formEl.innerHTML = `<div style="font-size:12px;color:#ef4444">Ошибка загрузки: ${_esc(e.message)}</div>`;
    }
  }

  async function saveAll() {
    if (!loaded) { await load(); }
    const payload = {};
    for (const name of Object.keys(schema)) {
      const value = _readField(name);
      const err = _clientValidate(name, value);
      if (err) { _status(err, false); return; }
      payload[name] = value;
    }
    try {
      await api.rulesSettingsSave(payload);
      _status('Все настройки сохранены ✓');
      showToast('Настройки ИИ сохранены', 'success');
    } catch (e) {
      _status('Ошибка: ' + e.message, false);
      showToast('Ошибка: ' + e.message, 'error');
    }
  }

  saveBtn?.addEventListener('click', saveAll);
  reloadBtn?.addEventListener('click', () => { loaded = false; load(); });
  // Lazy-load when the user first opens the sub-tab.
  document.querySelector('.rules__tab[data-rtab="ai"]')?.addEventListener('click', () => {
    if (!loaded) load();
  });
}


// =============================================================================
// Main export
// =============================================================================

export function initRules(ctx) {
  initRuleTabs();
  initEisenhower(ctx);
  initGtdRules(ctx);
  initStructuredRules(ctx);
  initToolsTab(ctx);
  initAiSettings(ctx);
}
