// =============================================================================
// projects.js — 3-column layout: list | detail | related + AI suggestions
// =============================================================================
import { api } from './api.js?v=20260523010000';

export function initProjects(ctx) {
  const { showToast } = ctx;

  // ── DOM refs ──────────────────────────────────────────────────────────────
  const listEl          = document.getElementById('projects-list');
  const emptyEl         = document.getElementById('projects-empty');
  const detailEl        = document.getElementById('projects-detail');
  const detailEmptyEl   = document.getElementById('projects-detail-empty');
  const detailTitleEl   = document.getElementById('projects-detail-title');
  const detailDescEl    = document.getElementById('projects-detail-desc');
  const detailIconEl    = document.getElementById('projects-detail-icon');
  const statusBadgeEl   = document.getElementById('projects-status-badge');
  const deadlineBadgeEl = document.getElementById('projects-deadline-badge');
  const progressBarEl   = document.getElementById('projects-progress-bar');
  const progressPctEl   = document.getElementById('projects-progress-pct');
  const goalsMetaEl     = document.getElementById('projects-goals-meta');
  const goalsListEl     = document.getElementById('projects-goals-list');
  const detailEditBtn   = document.getElementById('projects-detail-edit');
  const detailDeleteBtn = document.getElementById('projects-detail-delete');
  const addGoalBtn      = document.getElementById('projects-detail-add-goal');
  const goalAddForm     = document.getElementById('projects-goal-add-form');
  const goalAddInput    = document.getElementById('projects-goal-add-input');
  const goalAddDeadline = document.getElementById('projects-goal-add-deadline');
  const goalAddSaveBtn  = document.getElementById('projects-goal-add-save');
  const goalAddCancelBtn= document.getElementById('projects-goal-add-cancel');
  const suggestGoalBtn  = document.getElementById('projects-suggest-goal-btn');
  const newBtn          = document.getElementById('projects-new-btn');
  const modal           = document.getElementById('projects-modal');
  const modalTitle      = document.getElementById('projects-modal-title');
  const modalSave       = document.getElementById('projects-modal-save');
  const modalCancel     = document.getElementById('projects-modal-cancel');
  const pfName          = document.getElementById('pf-name');
  const pfDesc          = document.getElementById('pf-desc');
  const pfStatus        = document.getElementById('pf-status');
  const pfDeadline      = document.getElementById('pf-deadline');
  // Related panel
  const relatedContent  = document.getElementById('projects-related-content');
  const relatedEmpty    = document.getElementById('projects-related-empty');
  const aiCard          = document.getElementById('projects-ai-card');
  const aiText          = document.getElementById('projects-ai-suggestion-text');
  const aiAcceptBtn     = document.getElementById('projects-ai-accept');
  const aiDeclineBtn    = document.getElementById('projects-ai-decline');
  const vaultLinkInput  = document.getElementById('vault-link-input');
  const vaultLinkAddBtn = document.getElementById('vault-link-add-btn');
  const contactEmailInput = document.getElementById('contact-email-input');
  const contactNameInput  = document.getElementById('contact-name-input');
  const contactAddBtn     = document.getElementById('contact-add-btn');

  if (!listEl) return;

  let projects    = [];
  let selectedId  = null;
  let editingId   = null;
  let activeFilter = 'active';
  let currentSuggestion = null; // { suggestion, action, project_id }

  // ── Load ──────────────────────────────────────────────────────────────────
  async function loadProjects() {
    try {
      const data = await api.projectsList();
      projects = data.projects || [];
      renderList();
    } catch (err) {
      showToast('Ошибка загрузки проектов: ' + err.message, 'error');
      projects = [];
      renderList();
    }
  }

  // ── Filter tabs ───────────────────────────────────────────────────────────
  document.querySelectorAll('.projects__filter-tab').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.projects__filter-tab').forEach(b =>
        b.classList.remove('projects__filter-tab--active'));
      btn.classList.add('projects__filter-tab--active');
      activeFilter = btn.dataset.filter;
      renderList();
    });
  });

  function getFiltered() {
    return projects.filter(p => p.status === activeFilter);
  }

  function updateCounts() {
    const byStatus = { active: 0, paused: 0, done: 0 };
    projects.forEach(p => {
      const s = p.status || 'active';
      if (s in byStatus) byStatus[s]++;
    });
    document.getElementById('count-active').textContent  = byStatus.active;
    document.getElementById('count-paused').textContent  = byStatus.paused;
    document.getElementById('count-done').textContent    = byStatus.done;
  }

  // ── Render list ───────────────────────────────────────────────────────────
  function renderList() {
    updateCounts();
    const docs = getFiltered();
    listEl.innerHTML = '';

    if (!docs.length) {
      emptyEl.style.display = 'flex';
      return;
    }
    emptyEl.style.display = 'none';

    docs.forEach(p => {
      const goals    = p.goals || [];
      const done     = goals.filter(g => g.done).length;
      const pct      = goals.length ? Math.round(done / goals.length * 100) : 0;
      const deadlineLabel = _deadlineLabel(p.deadline);

      const item = document.createElement('div');
      item.className = `projects__list-item projects__list-item--${p.status || 'active'}${p.id === selectedId ? ' projects__list-item--selected' : ''}`;
      item.innerHTML = `
        <div class="projects__list-item-name">${_esc(p.name || 'Без названия')}</div>
        <div class="projects__list-item-bar-wrap">
          <div class="projects__list-item-bar" style="--pct:${pct}"></div>
        </div>
        <div class="projects__list-item-footer">
          <span>${pct}% · ${goals.length} целей</span>
          ${deadlineLabel ? `<span class="projects__list-item-deadline">${_esc(deadlineLabel)}</span>` : ''}
        </div>
      `;
      item.addEventListener('click', () => openDetail(p.id));
      listEl.appendChild(item);
    });
  }

  // ── Open detail ───────────────────────────────────────────────────────────
  function openDetail(id) {
    const p = projects.find(p => p.id === id);
    if (!p) return;
    selectedId = id;
    renderList();
    renderDetail(p);
    loadRelated(id);
    loadAssistantSuggestion(id);
  }

  function renderDetail(p) {
    // Switch visibility
    detailEmptyEl.style.display = 'none';
    detailEl.style.display      = 'flex';

    // Header
    detailIconEl.textContent       = _projectIcon(p.name);
    detailTitleEl.textContent      = p.name || 'Проект';
    detailDescEl.textContent       = p.description || '';

    // Status badge
    statusBadgeEl.textContent      = _statusLabel(p.status);
    statusBadgeEl.setAttribute('data-status', p.status || 'active');

    // Deadline badge
    const dl = _deadlineLabel(p.deadline);
    if (dl) {
      deadlineBadgeEl.textContent  = '🗓 ' + dl;
      deadlineBadgeEl.style.display = 'inline-flex';
    } else {
      deadlineBadgeEl.style.display = 'none';
    }

    // Progress
    const goals     = p.goals || [];
    const doneCount = goals.filter(g => g.done).length;
    const pct       = goals.length ? Math.round(doneCount / goals.length * 100) : 0;
    progressBarEl.style.setProperty('--pct', pct);
    progressPctEl.textContent = pct + '%';
    goalsMetaEl.textContent   = `${doneCount} из ${goals.length} целей выполнено`;

    // Goals
    renderGoals(p);
  }

  function renderGoals(p) {
    const goals = p.goals || [];
    goalsListEl.innerHTML = '';

    if (!goals.length) {
      goalsListEl.innerHTML = '<div style="font-size:13px;color:var(--color-text-muted);padding:8px 10px">Нет целей. Добавьте первую!</div>';
      return;
    }

    goals.forEach((g, idx) => {
      const el = document.createElement('div');
      el.className = 'projects__goal-item';
      el.innerHTML = `
        <input type="checkbox" ${g.done ? 'checked' : ''} data-idx="${idx}">
        <div class="projects__goal-item-body">
          <div class="projects__goal-item-title${g.done ? ' projects__goal-item-title--done' : ''}">${_esc(g.title || '')}</div>
          ${g.deadline ? `<div class="projects__goal-item-deadline">${_esc(g.deadline)}</div>` : ''}
        </div>
        <button class="projects__goal-item-delete" data-gid="${g.id}" title="Удалить">✕</button>
      `;

      el.querySelector('input').addEventListener('change', async e => {
        g.done = e.target.checked;
        await saveProject(p);
        renderDetail(p);
        renderList();
      });

      el.querySelector('.projects__goal-item-delete').addEventListener('click', async () => {
        try {
          await api.projectGoalDelete(p.id, g.id);
          p.goals.splice(idx, 1);
          p.progress = p.goals.length
            ? Math.round(p.goals.filter(g => g.done).length / p.goals.length * 100)
            : 0;
          renderDetail(p);
          renderList();
        } catch (err) {
          showToast('Ошибка удаления цели: ' + err.message, 'error');
        }
      });

      goalsListEl.appendChild(el);
    });
  }

  // ── Add goal form ─────────────────────────────────────────────────────────
  addGoalBtn?.addEventListener('click', () => {
    goalAddForm.style.display = 'flex';
    goalAddInput.value = '';
    goalAddDeadline.value = '';
    goalAddInput.focus();
  });

  goalAddCancelBtn?.addEventListener('click', () => {
    goalAddForm.style.display = 'none';
  });

  goalAddInput?.addEventListener('keydown', e => {
    if (e.key === 'Enter') goalAddSaveBtn.click();
    if (e.key === 'Escape') goalAddCancelBtn.click();
  });

  goalAddSaveBtn?.addEventListener('click', async () => {
    const title = goalAddInput.value.trim();
    if (!title) { showToast('Введите название цели', 'warning'); return; }
    const p = projects.find(p => p.id === selectedId);
    if (!p) return;
    try {
      const goal = await api.projectGoalAdd(selectedId, {
        title,
        done: false,
        deadline: goalAddDeadline.value || null,
      });
      p.goals = p.goals || [];
      p.goals.push(goal);
      goalAddForm.style.display = 'none';
      renderDetail(p);
      renderList();
    } catch (err) {
      showToast('Ошибка добавления цели: ' + err.message, 'error');
    }
  });

  // ── Suggest next goal ─────────────────────────────────────────────────────
  suggestGoalBtn?.addEventListener('click', async () => {
    if (!selectedId) return;
    suggestGoalBtn.disabled = true;
    suggestGoalBtn.textContent = '⏳ думаю…';
    try {
      const data = await api.projectSuggestGoal(selectedId);
      if (data.title) {
        goalAddInput.value = data.title;
        goalAddForm.style.display = 'flex';
        goalAddInput.focus();
        goalAddInput.select();
      }
    } catch (err) {
      showToast('Ошибка: ' + err.message, 'error');
    } finally {
      suggestGoalBtn.disabled = false;
      suggestGoalBtn.innerHTML = `
        <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" width="13" height="13"><path stroke-linecap="round" stroke-linejoin="round" d="M9.813 15.904L9 18.75l-.813-2.846a4.5 4.5 0 00-3.09-3.09L2.25 12l2.846-.813a4.5 4.5 0 003.09-3.09L9 5.25l.813 2.846a4.5 4.5 0 003.09 3.09L15.75 12l-2.846.813a4.5 4.5 0 00-3.09 3.09z"/></svg>
        предложить следующую цель`;
    }
  });

  // ── Load related panel ────────────────────────────────────────────────────
  async function loadRelated(id) {
    relatedEmpty.style.display  = 'none';
    relatedContent.style.display = 'block';

    try {
      const data = await api.projectRelated(id);
      renderRelated(data);
    } catch (err) {
      // graceful — just hide
      relatedContent.style.display = 'none';
      relatedEmpty.style.display   = 'flex';
    }
  }

  function renderRelated(data) {
    const mailRow      = document.getElementById('proj-rel-mails');
    const mailCount    = document.getElementById('proj-rel-mails-count');
    const mailSub      = document.getElementById('proj-rel-mails-sub');
    const meetRow      = document.getElementById('proj-rel-meetings');
    const meetCount    = document.getElementById('proj-rel-meetings-count');
    const meetSub      = document.getElementById('proj-rel-meetings-sub');
    const contactRow   = document.getElementById('proj-rel-contacts');
    const contactCount = document.getElementById('proj-rel-contacts-count');
    const contactSub   = document.getElementById('proj-rel-contacts-sub');
    const threadRow    = document.getElementById('proj-rel-threads');
    const threadCount  = document.getElementById('proj-rel-threads-count');
    const threadSub    = document.getElementById('proj-rel-threads-sub');

    // Mails
    if (data.mail_count > 0) {
      mailCount.textContent = `${data.mail_count} ${_plural(data.mail_count, 'письмо', 'письма', 'писем')}`;
      const contactNames = data.contact_names || [];
      mailSub.textContent = contactNames.slice(0, 3).join(' · ') || '';
      mailRow.style.display = 'flex';
    } else {
      mailRow.style.display = 'none';
    }

    // Meetings
    if (data.meeting_count > 0) {
      meetCount.textContent = `${data.meeting_count} ${_plural(data.meeting_count, 'встреча', 'встречи', 'встреч')}`;
      meetSub.textContent   = '';
      meetRow.style.display = 'flex';
    } else {
      meetRow.style.display = 'none';
    }

    // Contacts
    if (data.contact_count > 0) {
      contactCount.textContent = `${data.contact_count} ${_plural(data.contact_count, 'контакт', 'контакта', 'контактов')}`;
      const names = (data.contact_names || []);
      contactSub.textContent = names.length ? names[0] + ' в фокусе' : '';
      contactRow.style.display = 'flex';
    } else {
      contactRow.style.display = 'none';
    }

    // Chat threads
    if (data.thread_count > 0) {
      threadCount.textContent = `${data.thread_count} ${_plural(data.thread_count, 'чат-тред', 'чат-треда', 'чат-тредов')}`;
      const labels = (data.chat_threads || []).slice(0, 2).map(t => t.preview?.slice(0, 25) || '').filter(Boolean);
      threadSub.textContent = labels.join(', ') || '';
      threadRow.style.display = 'flex';
    } else {
      threadRow.style.display = 'none';
    }

    // Click handlers — open chat with context
    mailRow?.addEventListener('click', () => _openChatWithContext(data.mails?.[0]));
    meetRow?.addEventListener('click', () => _openChatWithContext(data.meetings?.[0]));
    threadRow?.addEventListener('click', () => {
      const tid = data.chat_threads?.[0]?.id;
      if (tid) window.dispatchEvent(new CustomEvent('pa:open-chat-thread', { detail: { tid } }));
    });
  }

  // ── Load AI assistant suggestion ──────────────────────────────────────────
  async function loadAssistantSuggestion(id) {
    aiCard.style.display = 'none';
    try {
      const data = await api.projectAssistantSuggests(id);
      if (data.suggestion) {
        currentSuggestion = data;
        aiText.textContent = data.suggestion;
        aiCard.style.display = 'block';
      }
    } catch (_) {
      // graceful — no suggestion shown
    }
  }

  aiAcceptBtn?.addEventListener('click', async () => {
    if (!currentSuggestion) return;
    const { action, project_id } = currentSuggestion;
    aiCard.style.display = 'none';
    currentSuggestion = null;

    if (action === 'open_chat' || action === 'book_slot') {
      const p = projects.find(p => p.id === project_id);
      const msg = `Помоги с проектом "${p?.name || ''}" — ${action === 'book_slot' ? 'забронируй слот в календаре' : 'следующий шаг'}`;
      window.dispatchEvent(new CustomEvent('pa:chat-send', { detail: { message: msg } }));
      // Switch to chat tab
      document.querySelector('.nav__item[data-tab="chat"]')?.click();
    } else if (action === 'summarize') {
      const p = projects.find(p => p.id === project_id);
      const msg = `/summarize проект ${p?.name || ''}`;
      window.dispatchEvent(new CustomEvent('pa:chat-send', { detail: { message: msg } }));
      document.querySelector('.nav__item[data-tab="chat"]')?.click();
    }
    showToast('Принято', 'success');
  });

  aiDeclineBtn?.addEventListener('click', () => {
    aiCard.style.display = 'none';
    currentSuggestion = null;
  });

  // ── Manage links ───────────────────────────────────────────────────────────
  vaultLinkAddBtn?.addEventListener('click', async () => {
    const vpath = vaultLinkInput?.value.trim();
    if (!vpath || !selectedId) { showToast('Введите путь к файлу vault', 'warning'); return; }
    try {
      await api.projectLinkVault(selectedId, vpath);
      if (vaultLinkInput) vaultLinkInput.value = '';
      showToast('Файл привязан', 'success');
      await loadRelated(selectedId);
    } catch (err) { showToast('Ошибка: ' + err.message, 'error'); }
  });

  contactAddBtn?.addEventListener('click', async () => {
    const email = contactEmailInput?.value.trim();
    if (!email || !email.includes('@') || !selectedId) {
      showToast('Введите корректный email', 'warning'); return;
    }
    try {
      await api.projectLinkContact(selectedId, {
        email,
        name: contactNameInput?.value.trim() || '',
      });
      if (contactEmailInput) contactEmailInput.value = '';
      if (contactNameInput)  contactNameInput.value  = '';
      showToast('Контакт добавлен', 'success');
      await loadRelated(selectedId);
    } catch (err) { showToast('Ошибка: ' + err.message, 'error'); }
  });

  // ── Save project ──────────────────────────────────────────────────────────
  async function saveProject(p) {
    try {
      await api.projectUpdate(p.id, {
        name:        p.name,
        description: p.description || '',
        status:      p.status || 'active',
        deadline:    p.deadline || null,
        goals:       p.goals || [],
      });
    } catch (err) {
      showToast('Ошибка сохранения: ' + err.message, 'error');
    }
  }

  // ── Edit / delete ─────────────────────────────────────────────────────────
  detailEditBtn?.addEventListener('click', () => {
    const p = projects.find(p => p.id === selectedId);
    if (p) openModal(p);
  });

  detailDeleteBtn?.addEventListener('click', async () => {
    if (!selectedId || !confirm('Удалить проект?')) return;
    try {
      await api.projectDelete(selectedId);
      projects = projects.filter(p => p.id !== selectedId);
      selectedId = null;
      detailEl.style.display = 'none';
      detailEmptyEl.style.display = 'flex';
      relatedContent.style.display = 'none';
      relatedEmpty.style.display   = 'flex';
      aiCard.style.display = 'none';
      renderList();
      showToast('Проект удалён', 'success');
    } catch (err) {
      showToast('Ошибка: ' + err.message, 'error');
    }
  });

  // ── Modal ─────────────────────────────────────────────────────────────────
  function openModal(project) {
    editingId = project ? project.id : null;
    modalTitle.textContent = project ? 'Редактировать проект' : 'Новый проект';
    pfName.value     = project?.name || '';
    pfDesc.value     = project?.description || '';
    pfStatus.value   = project?.status || 'active';
    pfDeadline.value = project?.deadline || '';
    modal.style.display = 'flex';
    pfName.focus();
  }

  function closeModal() {
    modal.style.display = 'none';
    editingId = null;
  }

  newBtn?.addEventListener('click', () => openModal(null));
  modalCancel?.addEventListener('click', closeModal);
  modal?.addEventListener('click', e => { if (e.target === modal) closeModal(); });

  modalSave?.addEventListener('click', async () => {
    const name = pfName.value.trim();
    if (!name) { showToast('Введите название проекта', 'warning'); return; }
    const body = {
      name,
      description: pfDesc.value.trim(),
      status:      pfStatus.value,
      deadline:    pfDeadline.value || null,
      goals:       editingId ? (projects.find(p => p.id === editingId)?.goals || []) : [],
    };
    try {
      if (editingId) {
        await api.projectUpdate(editingId, body);
        const idx = projects.findIndex(p => p.id === editingId);
        if (idx >= 0) {
          projects[idx] = { ...projects[idx], ...body };
          if (selectedId === editingId) renderDetail(projects[idx]);
        }
        showToast('Проект обновлён', 'success');
      } else {
        const created = await api.projectCreate({ ...body, goals: [] });
        projects.unshift(created);
        showToast('Проект создан', 'success');
        // Auto-select if filter matches
        if (activeFilter === created.status) {
          openDetail(created.id);
        }
      }
      closeModal();
      renderList();
    } catch (err) {
      showToast('Ошибка: ' + err.message, 'error');
    }
  });

  // ── Helpers ───────────────────────────────────────────────────────────────
  function _statusLabel(s) {
    return { active: 'active', paused: 'на паузе', done: 'завершён' }[s] || s || 'active';
  }

  function _deadlineLabel(d) {
    if (!d) return '';
    try {
      const date = new Date(d + 'T00:00:00');
      const today = new Date(); today.setHours(0,0,0,0);
      const diff = Math.round((date - today) / 86400000);
      if (diff < 0)  return 'просрочен';
      if (diff === 0) return 'сегодня';
      if (diff === 1) return 'завтра';
      const days = ['вс','пн','вт','ср','чт','пт','сб'];
      if (diff <= 7)  return days[date.getDay()];
      const months = ['янв','фев','мар','апр','май','июн','июл','авг','сен','окт','ноя','дек'];
      return `${date.getDate()} ${months[date.getMonth()]}`;
    } catch { return d; }
  }

  function _projectIcon(name) {
    if (!name) return '??';
    const words = name.trim().split(/\s+/);
    if (words.length >= 2) return (words[0][0] + words[1][0]).toUpperCase();
    return name.slice(0, 2).toUpperCase();
  }

  function _plural(n, one, few, many) {
    const mod10 = n % 10;
    const mod100 = n % 100;
    if (mod10 === 1 && mod100 !== 11) return `${n} ${one}`;
    if ([2,3,4].includes(mod10) && ![12,13,14].includes(mod100)) return `${n} ${few}`;
    return `${n} ${many}`;
  }

  function _esc(s) {
    return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  }

  function _openChatWithContext(vaultPath) {
    if (!vaultPath) return;
    window.dispatchEvent(new CustomEvent('pa:chat-send', {
      detail: { message: `@${vaultPath}`, suppressSend: true }
    }));
    document.querySelector('.nav__item[data-tab="chat"]')?.click();
  }

  // ── Init ──────────────────────────────────────────────────────────────────
  loadProjects();
}
