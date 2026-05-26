// =============================================================================
// chat.js — 3-column chat: threads | messages | Связи panel
// =============================================================================
import { api, streamText } from './api.js?v=20260520153419';
import { initContextPanel } from './context_ui.js?v=20260520153419';
import { formatMSKTime } from './time_utils.js?v=20260520153419';

// ── Markdown-like rendering ───────────────────────────────────────────────────
function escHtml(s) {
  return String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function renderMarkdown(text) {
  let html = escHtml(text)
    .replace(/```([\s\S]*?)```/g, '<pre><code>$1</code></pre>')
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/\*(.+?)\*/g, '<em>$1</em>')
    .replace(/^### (.+)$/gm, '<h3>$1</h3>')
    .replace(/^## (.+)$/gm, '<h2>$1</h2>')
    .replace(/^# (.+)$/gm, '<h1>$1</h1>')
    .replace(/^[\-\*] (.+)$/gm, '<li>$1</li>');

  html = html.replace(/(<li>.*?<\/li>)(\s*<li>.*?<\/li>)*/gs, '<ul>$&</ul>');
  html = html.replace(/<ul><ul>/g, '<ul>').replace(/<\/ul><\/ul>/g, '</ul>');
  html = html.replace(/\n{2,}/g, '</p><p>').replace(/\n/g, '<br>');
  return '<p>' + html + '</p>';
}

// ── Tool badge detection ──────────────────────────────────────────────────────
const TOOL_BADGE_RE = /\[tool:([^\]]+)\]/g;

function stripToolBadges(text) {
  return text.replace(TOOL_BADGE_RE, '').trim();
}

function extractToolBadges(text) {
  const badges = [];
  let m;
  while ((m = TOOL_BADGE_RE.exec(text)) !== null) badges.push(m[1]);
  TOOL_BADGE_RE.lastIndex = 0;
  return badges;
}

// ── Context chip type detection ───────────────────────────────────────────────
function chipType(path) {
  if (!path) return 'default';
  const p = path.toLowerCase();
  if (p.includes('/outlook/') || p.includes('/mail/') || p.endsWith('.eml')) return 'mail';
  if (p.includes('/calendar/')) return 'calendar';
  if (p.endsWith('.md')) return 'doc';
  return 'default';
}

const CHIP_ICONS = { mail: '✉️', calendar: '📅', doc: '📄', default: '📎' };

export function initChat(ctx) {
  const { showToast, activateTab } = ctx;

  // ── DOM refs ────────────────────────────────────────────────────────────────
  const historyList       = document.getElementById('chat-history-list');
  const messageArea       = document.getElementById('chat-messages');
  const refsArea          = document.getElementById('chat-refs');
  const textarea          = document.getElementById('chat-textarea');
  const sendBtn           = document.getElementById('chat-send');
  const newThreadBtn      = document.getElementById('chat-new-thread');
  const clearThreadBtn    = document.getElementById('chat-clear-thread');
  const deleteThreadBtn   = document.getElementById('chat-delete-thread');
  const clearAllThreadsBtn= document.getElementById('chat-clear-all-threads');
  const threadActionsEl   = document.getElementById('chat-thread-actions');
  const modeBadge         = document.getElementById('chat-mode-badge');
  const titleEl           = document.getElementById('chat-title');
  const contextChipsEl    = document.getElementById('chat-context-chips');
  const mentionPopup      = document.getElementById('chat-mention-popup');
  const mentionList       = document.getElementById('chat-mention-list');
  const slashPopup        = document.getElementById('chat-slash-popup');
  const slashList         = document.getElementById('chat-slash-list');
  const atBtn             = document.getElementById('chat-at-btn');
  const slashBtn          = document.getElementById('chat-slash-btn');
  const relatedBody       = document.getElementById('chat-related-body');

  if (!textarea) return;

  const contextPanel = initContextPanel('chat-context-panel');

  // ── State ───────────────────────────────────────────────────────────────────
  let threads               = [];
  let currentThread         = null;   // { id, title, messages: [] }
  let currentMode           = 'chat';
  let currentVaultThreadId  = null;
  let currentReplyMessageId = null;
  let currentThreadContext  = null;   // Stage 4: full draft context from /draft-context API
  let contextPaths          = [];
  let streaming             = false;
  let mentionQuery          = null;
  let slashOpen             = false;
  let popupIdx              = -1;
  let popupItems            = [];
  let relatedDebounce       = null;

  // ── Mode management ─────────────────────────────────────────────────────────
  const MODE_LABELS = {
    chat:      'чат',
    search:    'поиск',
    summarize: 'summarize',
    draft:     'draft',
  };

  function setMode(mode) {
    currentMode = mode;
    if (modeBadge) modeBadge.textContent = MODE_LABELS[mode] || mode;
    // Sync mode-tabs bar
    document.querySelectorAll('.chat__mode-tab').forEach(b =>
      b.classList.toggle('chat__mode-tab--active', b.dataset.mode === mode));
    // Legacy mode-btn (kept for compat)
    document.querySelectorAll('.chat__mode-btn').forEach(b =>
      b.classList.toggle('chat__mode-btn--active', b.dataset.mode === mode));
    // Draft action panel is dynamically injected by appendDraftEditPanel()
  }

  // Mode tab clicks
  document.querySelectorAll('.chat__mode-tab').forEach(btn =>
    btn.addEventListener('click', () => setMode(btn.dataset.mode)));

  // Legacy mode-btn (kept for backward compat)
  document.querySelectorAll('.chat__mode-btn').forEach(btn =>
    btn.addEventListener('click', () => setMode(btn.dataset.mode)));

  // ── Thread lifecycle ─────────────────────────────────────────────────────────
  async function loadThreads() {
    try {
      const data = await api.chatThreads();
      threads = data.threads || [];
      if (!currentThread) {
        if (threads.length) await switchThread(threads[0].id, false);
        else await createNewThread(false);
      }
      renderHistory();
    } catch (err) {
      showToast('Ошибка загрузки тредов: ' + err.message, 'error');
    }
  }

  async function switchThread(tid, _save = true) {
    const t = threads.find(x => x.id === tid);
    if (!t) return;
    currentThread = { id: t.id, title: t.title, messages: [] };
    contextPaths = [];
    currentVaultThreadId  = null;
    currentReplyMessageId = null;
    contextPanel.hide();
    renderContextChips();
    try {
      const data = await api.chatHistory(tid);
      currentThread.messages = (data.messages || []).map(m => ({
        role: m.role, content: m.content, created_at: m.created_at,
      }));
    } catch (err) {
      showToast('Ошибка загрузки истории: ' + err.message, 'error');
    }
    renderHistory();
    renderMessages();
    renderRefs();
    if (titleEl) titleEl.textContent = currentThread.title || 'Новый чат';
    scheduleRelatedRefresh(tid);
  }

  async function createNewThread(focus = true) {
    const placeholder = { id: 'local_' + Date.now(), title: 'Новый чат', messages: [] };
    threads.unshift(placeholder);
    currentThread = placeholder;
    contextPaths = [];
    currentVaultThreadId  = null;
    currentReplyMessageId = null;
    renderHistory();
    renderMessages();
    renderRefs();
    renderContextChips();
    clearRelated();
    if (titleEl) titleEl.textContent = 'Новый чат';
    if (focus) textarea.focus();
  }

  async function clearCurrentThread() {
    if (!currentThread) return;
    if (!confirm('Очистить историю сообщений в этом треде?')) return;
    try {
      await api.chatClear(currentThread.id);
      currentThread.messages = [];
      renderMessages();
      showToast('История очищена', 'success');
    } catch (err) {
      showToast('Ошибка: ' + err.message, 'error');
    }
  }

  async function deleteCurrentThread() {
    if (!currentThread) return;
    if (!confirm('Удалить тред полностью? Это необратимо.')) return;
    try {
      await api.chatDelete(currentThread.id);
      threads = threads.filter(t => t.id !== currentThread.id);
      currentThread = null;
      clearRelated();
      await loadThreads();
      showToast('Тред удалён', 'success');
    } catch (err) {
      showToast('Ошибка: ' + err.message, 'error');
    }
  }

  newThreadBtn?.addEventListener('click', () => createNewThread(true));
  clearThreadBtn?.addEventListener('click', clearCurrentThread);
  deleteThreadBtn?.addEventListener('click', deleteCurrentThread);

  clearAllThreadsBtn?.addEventListener('click', async () => {
    const count = threads.length;
    if (!count) { showToast('История уже пуста', 'info'); return; }
    if (!confirm(`Удалить все ${count} чатов из истории? Это необратимо.`)) return;
    try {
      await api.chatDeleteAll();
      threads = [];
      currentThread = null;
      renderHistory();
      renderMessages();
      clearRelated();
      if (titleEl) titleEl.textContent = 'Новый чат';
      showToast('История чатов очищена', 'success');
    } catch (err) {
      showToast('Ошибка: ' + err.message, 'error');
    }
  });

  // ── History rendering ────────────────────────────────────────────────────────
  function renderHistory() {
    if (!historyList) return;
    historyList.innerHTML = '';
    threads.forEach(t => {
      const el = document.createElement('div');
      const isActive = t.id === currentThread?.id;
      el.className = 'chat__thread-item' + (isActive ? ' chat__thread-item--active' : '');

      const titleEl2 = document.createElement('span');
      titleEl2.className = 'chat__thread-item-title';
      titleEl2.textContent = t.title || 'Чат';

      const metaEl = document.createElement('div');
      metaEl.className = 'chat__thread-item-meta';

      if (t.message_count != null && t.message_count > 0) {
        const countEl = document.createElement('span');
        countEl.className = 'chat__thread-item-count';
        countEl.textContent = t.message_count;
        metaEl.appendChild(countEl);
      }

      if (t.updated_at) {
        const timeEl = document.createElement('span');
        timeEl.className = 'chat__thread-item-time';
        timeEl.textContent = formatMSKTime(t.updated_at);
        metaEl.appendChild(timeEl);
      }

      el.appendChild(titleEl2);
      el.appendChild(metaEl);
      el.addEventListener('click', () => switchThread(t.id));
      historyList.appendChild(el);
    });

    // Show/hide thread actions bar
    if (threadActionsEl) {
      threadActionsEl.style.display = currentThread && !currentThread.id.startsWith('local_')
        ? 'flex' : 'none';
    }
  }

  // ── Message rendering ────────────────────────────────────────────────────────
  function renderMessages() {
    if (!messageArea) return;
    messageArea.innerHTML = '';
    const msgs = currentThread?.messages || [];
    msgs.forEach(m => appendMessageBubble(m.role, m.content, false, m.created_at));
    scrollBottom();
  }

  function appendMessageBubble(role, content, scroll = true, timestamp = null) {
    const wrap = document.createElement('div');
    wrap.className = `chat__message chat__message--${role}`;

    const avatar = document.createElement('div');
    avatar.className = 'chat__avatar';
    avatar.textContent = role === 'user' ? 'Я' : 'AI';

    const cleanContent = stripToolBadges(content);
    const badges = extractToolBadges(content);

    const meta = document.createElement('div');
    meta.style.cssText = 'font-size:10px;color:var(--color-text-faint);margin-bottom:2px;';
    meta.textContent = timestamp ? formatMSKTime(timestamp) : '';

    const bubble = document.createElement('div');
    bubble.className = 'chat__bubble';
    bubble.innerHTML = renderMarkdown(cleanContent);

    // Append tool-call badges
    badges.forEach(name => {
      const badge = document.createElement('span');
      badge.className = 'chat__tool-badge';
      badge.innerHTML = `🔧 ${escHtml(name)}`;
      bubble.appendChild(badge);
    });

    const col = document.createElement('div');
    col.style.cssText = 'display:flex;flex-direction:column;min-width:0;flex:1;';
    if (meta.textContent) col.appendChild(meta);
    col.appendChild(bubble);

    // Action bar for assistant messages (copy + draft)
    if (role === 'assistant') {
      col.appendChild(_buildBubbleActions(cleanContent, wrap));
    }

    wrap.appendChild(avatar);
    wrap.appendChild(col);
    messageArea.appendChild(wrap);
    if (scroll) scrollBottom();
    return bubble;
  }

  /** Build a small action bar placed below an assistant bubble. */
  function _buildBubbleActions(textContent, wrapEl) {
    const bar = document.createElement('div');
    bar.className = 'chat__bubble-actions';

    // Copy button
    const copyBtn = document.createElement('button');
    copyBtn.className = 'chat__bubble-action-btn';
    copyBtn.title = 'Скопировать';
    copyBtn.textContent = '📋';
    copyBtn.addEventListener('click', () => {
      navigator.clipboard.writeText(textContent).then(() => {
        copyBtn.textContent = '✓';
        setTimeout(() => { copyBtn.textContent = '📋'; }, 1500);
      });
    });

    // Save-to-draft button — one-click: directly call the Mail API.
    //
    // Behaviour:
    //   • If the chat has a reply context (currentReplyMessageId set, e.g.
    //     opened from inbox or /draft <message>) → the backend wires the
    //     draft as a reply to that thread; we pre-fetch sender + subject
    //     so the toast is informative.
    //   • Otherwise → the draft is created as a new outgoing message; the
    //     Mail compose window opens so the user can add recipients.
    //
    // Power-user override: "✏️ Изменить" beside the button still opens
    // the full edit panel for tweaking To/CC/Subject/Body before saving.
    const draftBtn = document.createElement('button');
    draftBtn.className = 'chat__bubble-action-btn chat__bubble-action-btn--draft';
    draftBtn.title = currentReplyMessageId
      ? 'Создать черновик ответа на письмо в Mail'
      : 'Создать новый черновик в Mail';
    draftBtn.textContent = currentReplyMessageId ? '↩️ Ответ' : '✉️ Черновик';
    draftBtn.addEventListener('click', () => _oneClickDraft(draftBtn, textContent, currentReplyMessageId));

    const editBtn = document.createElement('button');
    editBtn.className = 'chat__bubble-action-btn chat__bubble-action-btn--edit-draft';
    editBtn.title = 'Открыть редактор черновика';
    editBtn.textContent = '✏️';
    editBtn.addEventListener('click', () => {
      const existing = wrapEl.parentElement?.querySelector('.dp');
      if (existing) existing.remove();
      appendDraftEditPanel(wrapEl, textContent, '', currentReplyMessageId);
    });

    bar.appendChild(copyBtn);
    bar.appendChild(draftBtn);
    bar.appendChild(editBtn);
    return bar;
  }

  /**
   * One-click "save draft" action — calls /api/chat/save-draft-mail directly.
   * Handles both reply-to-thread (when replyMsgId is set) and new-mail flows.
   */
  async function _oneClickDraft(btn, body, replyMsgId) {
    if (!body || !body.trim()) {
      showToast('Текст пустой', 'warning');
      return;
    }
    const origText = btn.textContent;
    btn.disabled = true;
    btn.textContent = '⏳';

    try {
      // Derive a fallback subject from the first non-empty line.
      const firstLine = body.split('\n').find(l => l.trim()) || '';
      let subject = firstLine.replace(/^#+\s*/, '').trim().slice(0, 120);

      let toRecipients = [];
      let ccRecipients = [];

      // If this assistant message was generated as a reply to a real Mail
      // message, fetch the sender/subject so the backend can wire it as
      // a true reply and the toast is informative.
      if (replyMsgId) {
        try {
          const r = await fetch(
            `/api/chat/mail/message-meta?message_id=${encodeURIComponent(replyMsgId)}`,
          );
          if (r.ok) {
            const meta = await r.json();
            if (meta.sender_email) toRecipients = [meta.sender_email];
            if (Array.isArray(meta.cc) && meta.cc.length) ccRecipients = meta.cc;
            if (meta.subject) {
              const cleaned = meta.subject.replace(/^(Re:\s*)+/i, '');
              subject = cleaned ? `Re: ${cleaned}` : subject;
            }
          }
        } catch (_) {
          // Fall through with empty recipients — Mail.app will prompt user.
        }
      }

      const resp = await fetch('/api/chat/save-draft-mail', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          subject:             subject || (replyMsgId ? 'Re:' : 'Без темы'),
          body:                body.replace(/^#+\s+[^\n]*\n?/, '').trim(),
          to_recipients:       toRecipients,
          cc_recipients:       ccRecipients,
          reply_to_message_id: replyMsgId || null,
          save_to_drafts:      false,  // open compose window for review
        }),
      });

      if (!resp.ok) {
        const err = await resp.json().catch(() => ({ detail: resp.statusText }));
        throw new Error(err.detail || resp.statusText);
      }

      const data = await resp.json();
      showToast(data.message || 'Черновик создан', 'success');
      btn.textContent = '✓ Открыто';
      setTimeout(() => { btn.textContent = origText; }, 2500);
    } catch (err) {
      showToast('Ошибка создания черновика: ' + err.message, 'error');
      btn.textContent = origText;
    } finally {
      btn.disabled = false;
    }
  }

  function appendStreamingBubble() {
    const wrap = document.createElement('div');
    wrap.className = 'chat__message chat__message--assistant';

    const avatar = document.createElement('div');
    avatar.className = 'chat__avatar';
    avatar.textContent = 'AI';

    const bubble = document.createElement('div');
    bubble.className = 'chat__bubble chat__bubble--streaming';

    const col = document.createElement('div');
    col.style.cssText = 'display:flex;flex-direction:column;min-width:0;flex:1;';
    col.appendChild(bubble);

    wrap.appendChild(avatar);
    wrap.appendChild(col);
    messageArea.appendChild(wrap);
    scrollBottom();
    return { bubble, col, wrap };
  }

  function scrollBottom() {
    if (messageArea) messageArea.scrollTop = messageArea.scrollHeight;
  }

  // ── Context chips (header, near title) ──────────────────────────────────────
  function renderContextChips() {
    if (!contextChipsEl) return;
    contextChipsEl.innerHTML = '';
    contextPaths.forEach((p, i) => {
      const type = chipType(p);
      const name = p.split('/').pop().replace(/\.md$/, '');
      const chip = document.createElement('span');
      chip.className = `chat__context-chip chat__context-chip--${type}`;
      chip.title = p;
      chip.innerHTML = `${CHIP_ICONS[type]} ${escHtml(name)}<button data-idx="${i}" title="Убрать">×</button>`;
      contextChipsEl.appendChild(chip);
    });
    contextChipsEl.style.display = contextPaths.length ? 'flex' : 'none';
  }

  contextChipsEl?.addEventListener('click', e => {
    const btn = e.target.closest('button[data-idx]');
    if (!btn) return;
    contextPaths.splice(Number(btn.dataset.idx), 1);
    renderContextChips();
    renderRefs();
  });

  // ── Context refs (legacy @ chips above input) ────────────────────────────────
  function renderRefs() {
    if (!refsArea) return;
    refsArea.innerHTML = '';
    contextPaths.forEach((p, i) => {
      const chip = document.createElement('div');
      chip.className = 'chat__ref-chip';
      const name = p.split('/').pop().replace('.md', '');
      chip.innerHTML = `<span>${escHtml(name)}</span><button class="chat__ref-remove" data-idx="${i}">✕</button>`;
      refsArea.appendChild(chip);
    });
    refsArea.style.display = contextPaths.length ? 'flex' : 'none';
  }

  refsArea?.addEventListener('click', e => {
    const btn = e.target.closest('.chat__ref-remove');
    if (!btn) return;
    contextPaths.splice(Number(btn.dataset.idx), 1);
    renderRefs();
    renderContextChips();
  });

  // ── Stage 4: Thread context chip ─────────────────────────────────────────────
  /** Render or remove the 🧵 thread-context chip above the textarea. */
  function renderThreadContextChip() {
    // Remove any existing chip first
    contextChipsEl?.querySelectorAll('.chat__context-chip--thread').forEach(el => el.remove());

    if (!currentThreadContext || !contextChipsEl) return;

    const n = currentThreadContext.message_count || 0;
    const subject = currentThreadContext.subject || '';
    const hint = currentThreadContext.draft_hint || '';
    const label = n > 0 ? `🧵 Тред: ${n} ${pluralMsg(n)}` : '🧵 Тред';
    const title = [subject && `«${subject}»`, hint].filter(Boolean).join(' · ');

    const chip = document.createElement('span');
    chip.className = 'chat__context-chip chat__context-chip--thread';
    chip.title = title;
    chip.innerHTML = `${escHtml(label)}<button class="chat__thread-chip-info" title="Показать тред">?</button><button class="chat__thread-chip-remove" title="Убрать контекст треда">×</button>`;
    chip.querySelector('.chat__thread-chip-info')?.addEventListener('click', e => {
      e.stopPropagation();
      showThreadDrawer();
    });
    chip.querySelector('.chat__thread-chip-remove')?.addEventListener('click', e => {
      e.stopPropagation();
      currentThreadContext = null;
      renderThreadContextChip();
    });
    // Insert at start of chips row
    contextChipsEl.insertBefore(chip, contextChipsEl.firstChild);
    contextChipsEl.style.display = 'flex';
  }

  function pluralMsg(n) {
    if (n % 10 === 1 && n % 100 !== 11) return 'письмо';
    if ([2, 3, 4].includes(n % 10) && ![12, 13, 14].includes(n % 100)) return 'письма';
    return 'писем';
  }

  /** Show a modal-drawer with thread message list. */
  function showThreadDrawer() {
    if (!currentThreadContext) return;
    const existing = document.getElementById('chat-thread-drawer');
    if (existing) { existing.remove(); return; }   // toggle

    const msgs = currentThreadContext.thread_messages || [];
    const subject = currentThreadContext.subject || '';
    const facts = currentThreadContext.key_facts || [];

    const drawer = document.createElement('div');
    drawer.id = 'chat-thread-drawer';
    drawer.className = 'chat__thread-drawer';
    drawer.innerHTML = `
      <div class="chat__thread-drawer-header">
        <strong>🧵 ${escHtml(subject)}</strong>
        <button class="chat__thread-drawer-close" title="Закрыть">×</button>
      </div>
      <div class="chat__thread-drawer-body">
        ${facts.length ? `<div class="chat__thread-facts"><strong>Ключевые факты:</strong><ul>${facts.map(f => `<li>${escHtml(f)}</li>`).join('')}</ul></div>` : ''}
        <div class="chat__thread-msgs">
          ${msgs.map(m => {
            const roleClass = m.is_mine ? 'outgoing' : 'incoming';
            const roleLabel = m.is_mine ? '↑ Я' : `↓ ${escHtml(m.sender || '?')}`;
            const dateStr = (m.date || '').slice(0, 10);
            const bodySnip = escHtml((m.body || '').slice(0, 300)) + ((m.body || '').length > 300 ? '…' : '');
            return `<div class="chat__thread-msg chat__thread-msg--${roleClass}">
              <div class="chat__thread-msg-meta">${escHtml(roleLabel)} · ${escHtml(dateStr)}</div>
              <div class="chat__thread-msg-body">${bodySnip}</div>
            </div>`;
          }).join('')}
        </div>
      </div>`;
    drawer.querySelector('.chat__thread-drawer-close').addEventListener('click', () => drawer.remove());
    // Append inside chat panel so it floats above textarea
    const chatPanel = document.querySelector('.chat__input-area') || document.body;
    chatPanel.appendChild(drawer);
  }

  // ── Связи (related) panel ─────────────────────────────────────────────────────
  function clearRelated() {
    if (!relatedBody) return;
    relatedBody.innerHTML = `
      <div class="chat__related-empty">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
          <path d="M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101"/>
          <path d="M10.172 13.828a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1"/>
        </svg>
        <span>Нет связанных объектов</span>
      </div>`;
  }

  function scheduleRelatedRefresh(tid) {
    clearTimeout(relatedDebounce);
    relatedDebounce = setTimeout(() => loadRelated(tid), 600);
  }

  async function loadRelated(tid) {
    if (!relatedBody || !tid || tid.startsWith('local_')) {
      clearRelated();
      return;
    }
    try {
      const data = await fetch(`/api/chat/related?thread_id=${encodeURIComponent(tid)}`).then(r => r.json());
      renderRelated(data);
    } catch {
      clearRelated();
    }
  }

  function renderRelated(data) {
    if (!relatedBody) return;
    const { contacts = [], projects = [], threads: mailThreads = [], events = [] } = data;

    if (!contacts.length && !projects.length && !mailThreads.length && !events.length) {
      clearRelated();
      return;
    }

    relatedBody.innerHTML = '';

    function addSection(label, items, icon, onClick) {
      if (!items.length) return;
      const sec = document.createElement('div');
      sec.className = 'chat__related-section';
      sec.textContent = label;
      relatedBody.appendChild(sec);
      items.forEach(item => {
        const card = document.createElement('div');
        card.className = 'chat__related-card';
        card.innerHTML = `
          <span class="chat__related-card-icon">${icon}</span>
          <div class="chat__related-card-body">
            <span class="chat__related-card-title">${escHtml(item.name || item.title || item.subject || '')}</span>
            <span class="chat__related-card-sub">${escHtml(item.sub || item.email || item.date || '')}</span>
          </div>`;
        card.addEventListener('click', () => onClick(item));
        relatedBody.appendChild(card);
      });
    }

    addSection('Контакты', contacts, '👤', item => {
      if (item.email) {
        textarea.value += ` @${item.email}`;
        textarea.focus();
        resizeTextarea();
      }
    });

    addSection('Письма', mailThreads, '✉️', item => {
      if (item.path) {
        if (!contextPaths.includes(item.path)) {
          contextPaths.push(item.path);
          renderContextChips();
          renderRefs();
        }
      }
    });

    addSection('Встречи', events, '📅', item => {
      if (item.path && !contextPaths.includes(item.path)) {
        contextPaths.push(item.path);
        renderContextChips();
        renderRefs();
      }
    });

    addSection('Проекты', projects, '📁', item => {
      showToast(`Проект: ${item.name || item.title}`, 'info');
    });
  }

  // ── Send message ─────────────────────────────────────────────────────────────
  async function sendMessage() {
    if (streaming) return;
    const text = textarea.value.trim();
    if (!text) return;

    textarea.value = '';
    resizeTextarea();

    if (!currentThread) await createNewThread(false);
    currentThread.messages.push({ role: 'user', content: text });
    appendMessageBubble('user', text);

    if (currentThread.title === 'Новый чат' || !currentThread.title) {
      currentThread.title = text.slice(0, 40) + (text.length > 40 ? '…' : '');
      if (titleEl) titleEl.textContent = currentThread.title;
      renderHistory();
    }

    contextPanel.render({
      mode: currentMode,
      vault_refs: contextPaths.map(p => ({ path: p, label: p })),
      tool_specs: [],
    });

    streaming = true;
    if (sendBtn) sendBtn.disabled = true;
    const { bubble, col, wrap } = appendStreamingBubble();
    let accumulated = '';

    try {
      const wasLocal = currentThread.id.startsWith('local_');
      const streamResult = await streamText('/api/chat/send', {
        thread_id: wasLocal ? null : currentThread.id,
        message: text,
        mode: currentMode,
        context_paths: contextPaths,
        vault_thread_id: currentVaultThreadId || null,
        reply_message_id: currentReplyMessageId || null,   // BUG-2 fix
      }, chunk => {
        accumulated += chunk;
        bubble.innerHTML = renderMarkdown(stripToolBadges(accumulated));
        scrollBottom();
      });

      bubble.classList.remove('chat__bubble--streaming');

      // Render final tool badges
      const badges = extractToolBadges(accumulated);
      badges.forEach(name => {
        const badge = document.createElement('span');
        badge.className = 'chat__tool-badge';
        badge.innerHTML = `🔧 ${escHtml(name)}`;
        col.appendChild(badge);
      });

      currentThread.messages.push({ role: 'assistant', content: accumulated });

      // Bind server-assigned thread ID from X-Thread-ID header
      if (wasLocal && streamResult.threadId) {
        currentThread.id = streamResult.threadId;
      }
      if (wasLocal) {
        await loadThreads();
      }

      // Refresh Связи panel after response
      if (currentThread && !currentThread.id.startsWith('local_')) {
        scheduleRelatedRefresh(currentThread.id);
      }

      // Add action bar (copy + draft) to the finalized streaming bubble
      const cleanAccumulated = stripToolBadges(accumulated);
      col.appendChild(_buildBubbleActions(cleanAccumulated, wrap));

      // Draft mode — auto-open inline draft panel
      if (currentMode === 'draft' && accumulated.trim()) {
        appendDraftEditPanel(
          wrap,
          accumulated,
          text,
          currentReplyMessageId,
        );
      }
    } catch (err) {
      console.error('[chat] sendMessage error:', err);
      bubble.innerHTML = '<p>Ошибка: ' + escHtml(err.message) + '</p>';
      bubble.classList.add('chat__bubble--error');
      showToast('Ошибка отправки: ' + err.message, 'error');
    } finally {
      streaming = false;
      if (sendBtn) sendBtn.disabled = false;
    }
  }

  sendBtn?.addEventListener('click', sendMessage);

  // ── Draft inline panel ───────────────────────────────────────────────────────
  async function appendDraftEditPanel(wrapEl, body, prompt, replyMsgId = null) {
    if (!wrapEl) return;

    let guessedSubject = '';
    const firstLine = body.split('\n').find(l => l.trim());
    if (firstLine && firstLine.length < 120) {
      guessedSubject = firstLine.replace(/^#+\s*/, '').trim();
    }
    if (!guessedSubject) guessedSubject = prompt.slice(0, 80);

    const panel = document.createElement('div');
    panel.className = 'dp';
    panel.innerHTML = `
      <div class="dp__header">
        <span>✉️ Черновик письма</span>
        <button class="dp__close" title="Закрыть">×</button>
      </div>
      <div class="dp__fields">
        <div class="dp__row">
          <span class="dp__label">Кому</span>
          <input class="dp__input dp__to" type="text" placeholder="email1, email2" autocomplete="off">
        </div>
        <div class="dp__row">
          <span class="dp__label">Копия</span>
          <input class="dp__input dp__cc" type="text" placeholder="необязательно" autocomplete="off">
        </div>
        <div class="dp__row">
          <span class="dp__label">Тема</span>
          <input class="dp__input dp__subject" type="text" value="${escHtml(guessedSubject)}">
        </div>
      </div>
      <textarea class="dp__body"></textarea>
      <div class="dp__actions">
        <button class="dp__btn dp__btn--primary dp__send">Отправить</button>
        <button class="dp__btn dp__save">Сохранить в черновиках</button>
        <button class="dp__btn dp__regen">Перегенерировать</button>
      </div>
      <div class="dp__status"></div>`;

    const toInput      = panel.querySelector('.dp__to');
    const ccInput      = panel.querySelector('.dp__cc');
    const subjectInput = panel.querySelector('.dp__subject');
    const bodyTA       = panel.querySelector('.dp__body');
    const sendDraftBtn = panel.querySelector('.dp__send');
    const saveBtn      = panel.querySelector('.dp__save');
    const regenBtn     = panel.querySelector('.dp__regen');
    const statusEl     = panel.querySelector('.dp__status');
    const closeBtn     = panel.querySelector('.dp__close');

    bodyTA.value = body.replace(/^#+\s+[^\n]*\n?/, '').trim();
    closeBtn.addEventListener('click', () => panel.remove());

    // Auto-load reply metadata
    if (replyMsgId) {
      statusEl.textContent = 'Загружаю адресатов…';
      try {
        const resp = await fetch(`/api/chat/mail/message-meta?message_id=${encodeURIComponent(replyMsgId)}`);
        if (resp.ok) {
          const meta = await resp.json();
          if (meta.sender_email) toInput.value = meta.sender_email;
          if (meta.cc && meta.cc.length) ccInput.value = meta.cc.join(', ');
          const orig = (meta.subject || '').replace(/^(Re:\s*)+/i, '');
          subjectInput.value = orig ? `Re: ${orig}` : guessedSubject;
          statusEl.textContent = '';
        } else {
          statusEl.textContent = 'Адресаты не найдены — введите вручную';
        }
      } catch {
        statusEl.textContent = 'Не удалось загрузить адресатов';
      }
    }

    function parseEmails(raw) {
      return raw.split(',').map(s => s.trim()).filter(s => s.includes('@'));
    }

    async function sendToMail(saveToDrafts) {
      const subject = subjectInput.value.trim();
      const bodyText = bodyTA.value;
      const toList   = parseEmails(toInput.value);
      const ccList   = parseEmails(ccInput.value);

      if (!subject)        { showToast('Укажите тему письма', 'warning'); return; }
      if (!bodyText.trim()){ showToast('Тело письма пустое', 'warning'); return; }

      sendDraftBtn.disabled = true;
      saveBtn.disabled = true;
      statusEl.textContent = saveToDrafts ? 'Сохраняю в черновиках…' : 'Открываю в Mail…';

      try {
        const resp = await fetch('/api/chat/save-draft-mail', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            subject,
            body:                bodyText,
            to_recipients:       toList,
            cc_recipients:       ccList,
            reply_to_message_id: replyMsgId || null,
            save_to_drafts:      saveToDrafts,
          }),
        });
        if (!resp.ok) {
          const err = await resp.json().catch(() => ({ detail: resp.statusText }));
          throw new Error(err.detail || resp.statusText);
        }
        const data = await resp.json();
        showToast(data.message, 'success');
        statusEl.textContent = data.message;
        if (saveToDrafts) {
          saveBtn.textContent = 'Сохранено ✓';
        } else {
          sendDraftBtn.textContent = 'Открыто ✓';
        }
      } catch (err) {
        showToast('Ошибка: ' + err.message, 'error');
        statusEl.textContent = 'Ошибка: ' + err.message;
      } finally {
        sendDraftBtn.disabled = false;
        saveBtn.disabled = false;
      }
    }

    sendDraftBtn.addEventListener('click', () => sendToMail(false));
    saveBtn.addEventListener('click', () => sendToMail(true));

    // Перегенерировать — put the original prompt back into textarea and send
    regenBtn.addEventListener('click', () => {
      panel.remove();
      textarea.value = prompt;
      resizeTextarea();
      textarea.focus();
      sendMessage();
    });

    wrapEl.after(panel);
  }

  // Backward-compat alias
  function appendSaveToOutlookBtn(wrapEl, body, prompt, replyMsgId = null) {
    appendDraftEditPanel(wrapEl, body, prompt, replyMsgId);
  }

  // ── Textarea auto-resize ─────────────────────────────────────────────────────
  function resizeTextarea() {
    textarea.style.height = 'auto';
    textarea.style.height = Math.min(textarea.scrollHeight, 180) + 'px';
  }

  textarea?.addEventListener('input', () => {
    resizeTextarea();
    handlePopupTrigger();
  });

  textarea?.addEventListener('keydown', e => {
    if (mentionQuery !== null || slashOpen) {
      if (e.key === 'ArrowDown') { e.preventDefault(); movePopup(1); return; }
      if (e.key === 'ArrowUp')   { e.preventDefault(); movePopup(-1); return; }
      if (e.key === 'Enter')     { e.preventDefault(); selectPopupItem(); return; }
      if (e.key === 'Escape')    { closePopups(); return; }
    }
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  });

  // ── @ mention popup ──────────────────────────────────────────────────────────
  let mentionDebounce = null;

  function handlePopupTrigger() {
    const val = textarea.value;
    const pos = textarea.selectionStart;
    const before = val.slice(0, pos);

    const atMatch = before.match(/@(\w*)$/);
    if (atMatch) {
      mentionQuery = atMatch[1];
      slashOpen = false;
      if (slashPopup) slashPopup.style.display = 'none';
      clearTimeout(mentionDebounce);
      mentionDebounce = setTimeout(() => fetchMentions(mentionQuery), 200);
      return;
    }

    const slashMatch = val.match(/^\/(\w*)$/);
    if (slashMatch) {
      mentionQuery = null;
      slashOpen = true;
      if (mentionPopup) mentionPopup.style.display = 'none';
      showSlashPopup(slashMatch[1]);
      return;
    }

    closePopups();
  }

  async function fetchMentions(q) {
    try {
      const data = await api.vaultMention(q, 8);
      showMentionPopup(data.results || []);
    } catch {
      closePopups();
    }
  }

  function showMentionPopup(results) {
    popupItems = results;
    popupIdx = -1;
    if (!mentionList) return;
    mentionList.innerHTML = '';
    if (!results.length) {
      if (mentionPopup) mentionPopup.style.display = 'none';
      return;
    }
    results.forEach((r, i) => {
      const el = document.createElement('div');
      el.className = 'chat__popup-item';
      el.innerHTML = `
        <span class="chat__popup-item-title">${escHtml(r.title || r.path)}</span>
        <span class="chat__popup-item-section">${escHtml(r.section || '')}</span>`;
      el.addEventListener('mousedown', e => { e.preventDefault(); selectMentionItem(i); });
      mentionList.appendChild(el);
    });
    if (mentionPopup) mentionPopup.style.display = 'block';
  }

  function selectMentionItem(idx) {
    const item = popupItems[idx];
    if (!item) return;
    const val = textarea.value;
    const pos = textarea.selectionStart;
    const before = val.slice(0, pos);
    const after = val.slice(pos);
    const newBefore = before.replace(/@\w*$/, '');
    textarea.value = newBefore + after;
    textarea.setSelectionRange(newBefore.length, newBefore.length);
    resizeTextarea();
    if (!contextPaths.includes(item.path)) {
      contextPaths.push(item.path);
      renderRefs();
      renderContextChips();
    }
    closePopups();
  }

  // ── Slash popup ──────────────────────────────────────────────────────────────
  const SLASH_COMMANDS = [
    { id: 'chat',      label: 'Чат',              desc: 'Обычный разговор с ассистентом' },
    { id: 'search',    label: 'Поиск',             desc: 'Поиск по PersonalVault' },
    { id: 'summarize', label: 'Суммаризация',      desc: 'Краткое резюме по теме' },
    { id: 'draft',     label: 'Черновик ответа',   desc: 'Составить ответ на письмо' },
    { id: 'встреча',   label: 'Создать встречу',   desc: 'Создать событие в Calendar.app' },
    { id: 'событие',   label: 'Новое событие',     desc: 'Добавить событие в календарь' },
  ];

  function showSlashPopup(q) {
    const filtered = SLASH_COMMANDS.filter(c =>
      !q || c.id.startsWith(q) || c.label.toLowerCase().includes(q.toLowerCase())
    );
    popupItems = filtered;
    popupIdx = -1;
    if (!slashList) return;
    slashList.innerHTML = '';
    if (!filtered.length) {
      if (slashPopup) slashPopup.style.display = 'none';
      return;
    }
    filtered.forEach((c, i) => {
      const el = document.createElement('div');
      el.className = 'chat__popup-item';
      el.innerHTML = `
        <span class="chat__popup-item-title">${escHtml(c.label)}</span>
        <span class="chat__popup-item-section">${escHtml(c.desc)}</span>`;
      el.addEventListener('mousedown', e => { e.preventDefault(); selectSlashItem(i); });
      slashList.appendChild(el);
    });
    if (slashPopup) slashPopup.style.display = 'block';
  }

  function selectSlashItem(idx) {
    const item = popupItems[idx];
    if (!item) return;
    closePopups();

    // Stage 7: /встреча and /событие — route to calendar intent NLP
    if (item.id === 'встреча' || item.id === 'событие') {
      textarea.value = '';
      resizeTextarea();
      // Prompt user to type the event description
      const origPlaceholder = textarea.placeholder;
      textarea.placeholder = 'Опиши событие: «Встреча с Ивановым в пятницу в 15:00»…';
      textarea.focus();
      const _handleEventInput = (e) => {
        if (e.key === 'Escape') {
          textarea.placeholder = origPlaceholder;
          textarea.removeEventListener('keydown', _handleEventInput);
          return;
        }
        if (e.key !== 'Enter') return;
        e.preventDefault();
        const text = textarea.value.trim();
        if (!text) return;
        textarea.value = '';
        textarea.placeholder = origPlaceholder;
        resizeTextarea();
        textarea.removeEventListener('keydown', _handleEventInput);
        // Dispatch to today.js handler
        document.dispatchEvent(new CustomEvent('pa:create-event', { detail: { text } }));
      };
      textarea.addEventListener('keydown', _handleEventInput);
      return;
    }

    textarea.value = '';
    resizeTextarea();
    setMode(item.id);
  }

  function movePopup(dir) {
    const list = mentionQuery !== null ? mentionList : slashList;
    if (!list) return;
    const items = list.querySelectorAll('.chat__popup-item');
    if (!items.length) return;
    popupIdx = Math.max(0, Math.min(items.length - 1, popupIdx + dir));
    items.forEach((el, i) => el.classList.toggle('chat__popup-item--active', i === popupIdx));
  }

  function selectPopupItem() {
    if (popupIdx < 0) popupIdx = 0;
    if (mentionQuery !== null) selectMentionItem(popupIdx);
    else selectSlashItem(popupIdx);
  }

  function closePopups() {
    mentionQuery = null;
    slashOpen = false;
    if (mentionPopup) mentionPopup.style.display = 'none';
    if (slashPopup)   slashPopup.style.display = 'none';
    popupIdx = -1;
    popupItems = [];
  }

  // ── Toolbar buttons ──────────────────────────────────────────────────────────
  atBtn?.addEventListener('click', () => {
    textarea.value += '@';
    textarea.focus();
    resizeTextarea();
    fetchMentions('');
  });

  slashBtn?.addEventListener('click', () => {
    textarea.value = '/';
    textarea.focus();
    resizeTextarea();
    showSlashPopup('');
  });

  document.addEventListener('click', e => {
    if (!e.target.closest('#chat-mention-popup') &&
        !e.target.closest('#chat-slash-popup') &&
        e.target !== textarea) {
      closePopups();
    }
  });

  // ── pa:chat-open event (from Vault / Inbox) ──────────────────────────────────
  document.addEventListener('pa:chat-open', async (e) => {
    const { path, mode, message, vault_thread_id, reply_message_id, thread_context } = e.detail || {};

    // BUG-1 fix: always open a fresh thread so inbox actions never pollute an
    // existing conversation.  createNewThread() resets contextPaths and all
    // currentXxx state vars, so we must call it *before* setting them below.
    await createNewThread(false);

    if (path && !contextPaths.includes(path)) {
      contextPaths.push(path);
      renderContextChips();
      renderRefs();
    }
    currentVaultThreadId  = vault_thread_id  || null;
    currentReplyMessageId = reply_message_id || null;
    // Stage 4: store thread context and render chip
    currentThreadContext  = thread_context   || null;
    renderThreadContextChip();
    const targetMode = mode || 'draft';  // default to draft when coming from inbox
    setMode(targetMode);
    if (message) {
      textarea.value = message;
      resizeTextarea();
    }
    textarea.focus();
  });

  // ── pa:chat-send event (auto-send from Vault / Inbox / Search) ──────────────
  document.addEventListener('pa:chat-send', async (e) => {
    const { message, mode, vault_thread_id, reply_message_id, path } = e.detail || {};
    if (!message) return;
    activateTab('chat');
    // E-3 fix: always open a fresh thread so inbox auto-send actions (summarize,
    // create-meeting) never pollute an existing conversation.
    // createNewThread() resets contextPaths/currentVaultThreadId/currentReplyMessageId,
    // so set them AFTER the await.
    await createNewThread(false);
    if (path && !contextPaths.includes(path)) {
      // Attach the source document so the backend has real context for
      // summarize/discuss actions dispatched from Search.
      contextPaths.push(path);
      renderContextChips();
      renderRefs();
    }
    currentVaultThreadId  = vault_thread_id  || null;
    currentReplyMessageId = reply_message_id || null;
    if (mode) setMode(mode);
    textarea.value = message;
    resizeTextarea();
    sendMessage();
  });

  // ── Init ─────────────────────────────────────────────────────────────────────
  clearRelated();
  loadThreads();
}
