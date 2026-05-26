// =============================================================================
// context_ui.js — Visualise active context badges (persona, vault, tools)
// =============================================================================

export function initContextPanel(containerId) {
  const container = document.getElementById(containerId);
  if (!container) return { render: () => {}, hide: () => {}, show: () => {} };

  const body = container.querySelector('.chat__context-body') || container;

  function render(ctx) {
    // ctx = { vault_refs: [...], mode: 'chat', tool_specs: [...] }
    const parts = [];

    // Mode badge
    const modeLabel = {
      chat: '💬 Чат',
      search: '🔍 Поиск',
      summarize: '📝 Тема',
      draft: '✉️ Ответ',
    }[ctx.mode] || '💬 Чат';
    parts.push(`<span class="chat__context-badge">${modeLabel}</span>`);

    // Vault refs
    (ctx.vault_refs || []).forEach(ref => {
      const name = ref.label.split('«')[1]?.split('»')[0] || ref.path.split('/').pop();
      parts.push(`<span class="chat__context-badge">📎 ${name}</span>`);
    });

    // Tools hint
    if ((ctx.tool_specs || []).length > 0) {
      parts.push(`<span class="chat__context-badge">🛠 ${ctx.tool_specs.length}</span>`);
    }

    body.innerHTML = parts.join('');
    container.style.display = parts.length > 1 ? 'block' : 'none';
  }

  function hide() { container.style.display = 'none'; }
  function show() { container.style.display = 'block'; }

  return { render, hide, show };
}
