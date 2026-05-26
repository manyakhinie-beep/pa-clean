// =============================================================================
// settings.js — Settings tab: general, souls, classify, tools, sync, testdata
// =============================================================================
import { api } from './api.js?v=20260520153419';

// ── OperationProgress — animated timebar for blocking API calls ───────────────
// Usage:
//   const p = new OperationProgress(document.getElementById('my-progress'));
//   p.start('Запуск…');
//   p.step('Применяю теги…');
//   p.finish('Готово: 42 файла', false);   // or true for error
class OperationProgress {
  constructor(containerEl) {
    this._el = containerEl;
    this._ticker = null;
    this._start = 0;
  }

  start(label = 'Выполняется…') {
    this._start = Date.now();
    this._el.style.display = '';
    this._render(label, false, false);
    clearInterval(this._ticker);
    this._ticker = setInterval(() => {
      const sec = ((Date.now() - this._start) / 1000).toFixed(0);
      const el = this._el.querySelector('.op-progress__elapsed');
      if (el) el.textContent = sec + 's';
    }, 500);
  }

  step(label) {
    const el = this._el.querySelector('.op-progress__step');
    if (el) el.textContent = label;
  }

  finish(message, isError = false) {
    clearInterval(this._ticker);
    this._ticker = null;
    const elapsed = ((Date.now() - this._start) / 1000).toFixed(1);
    this._renderDone(message, elapsed, isError);
    setTimeout(() => {
      this._el.style.display = 'none';
      this._el.innerHTML = '';
    }, 6000);
  }

  _render(label, _done, _err) {
    this._el.innerHTML = `
      <div class="op-progress">
        <div class="op-progress__bar">
          <div class="op-progress__fill op-progress__fill--indeterminate"></div>
        </div>
        <div class="op-progress__meta">
          <span class="op-progress__step">${label}</span>
          <span class="op-progress__elapsed">0s</span>
        </div>
      </div>`;
  }

  _renderDone(message, elapsed, isError) {
    const icon = isError ? '❌' : '✅';
    const color = isError ? 'var(--danger,#ef4444)' : 'var(--success,#22c55e)';
    this._el.innerHTML = `
      <div class="op-progress op-progress--done" style="border-color:${color}">
        <span style="color:${color}">${icon}</span>
        <span class="op-progress__step" style="flex:1">${message}</span>
        <span class="op-progress__elapsed">${elapsed}s</span>
      </div>`;
  }
}

export function initSettings(ctx) {
  const { showToast } = ctx;

  // ── Sub-tab switching ─────────────────────────────────────────────────────
  // Note: classify + tools sub-tabs were moved to the Rules tab and are no
  // longer present in the Settings HTML. Only wire up what still exists here.
  const tabBtns = document.querySelectorAll('.settings__tab-btn[data-stab]');
  const tabPanels = {
    general:  document.getElementById('stab-general'),
    souls:    document.getElementById('stab-souls'),
    sync:     document.getElementById('stab-sync'),
    testdata: document.getElementById('stab-testdata'),
  };

  function switchStab(id) {
    tabBtns.forEach(b => b.classList.toggle('settings__tab-btn--active', b.dataset.stab === id));
    Object.entries(tabPanels).forEach(([k, el]) => {
      if (el) el.style.display = k === id ? '' : 'none';
    });
    if (id === 'general')  { loadGeneral(); checkVault(); }
    if (id === 'souls')    { loadSouls(); loadPersona(); }
    if (id === 'sync')     { loadScheduleStatus(); }
    if (id === 'testdata') loadSnapshots();
  }

  tabBtns.forEach(btn => {
    btn.addEventListener('click', () => switchStab(btn.dataset.stab));
  });

  // ── General ───────────────────────────────────────────────────────────────
  const sMlxModel      = document.getElementById('s-mlx-model');
  const sVaultPath     = document.getElementById('s-vault-path');
  const sMaxTokens     = document.getElementById('s-max-tokens');
  const sTemp          = document.getElementById('s-temp');
  const sEmbPath       = document.getElementById('s-embedding-path');
  const sEmbModel      = document.getElementById('s-embedding-model');
  const sEmbSsl        = document.getElementById('s-embedding-ssl');
  const sHfToken       = document.getElementById('s-hf-token');
  const sHfTokenShow   = document.getElementById('s-hf-token-show');
  const sSaveBtn       = document.getElementById('s-save-general');
  const sCheckVault    = document.getElementById('s-check-vault');
  const sStatus        = document.getElementById('s-general-status');
  const sLastSyncRow   = document.getElementById('s-last-sync-row');
  const sLastSyncAt    = document.getElementById('s-last-sync-at');

  // ── Show/hide HF token ────────────────────────────────────────────────────
  sHfTokenShow?.addEventListener('click', () => {
    if (sHfToken) {
      sHfToken.type = sHfToken.type === 'password' ? 'text' : 'password';
    }
  });

  async function loadGeneral() {
    try {
      const data = await api.settingsGet();
      if (sMlxModel)  sMlxModel.value  = data.mlx_model_path  || '';
      if (sVaultPath) sVaultPath.value = data.vault_path       || '';
      if (sMaxTokens) sMaxTokens.value = data.mlx_max_tokens   || 1024;
      if (sTemp)      sTemp.value      = data.mlx_temperature  || 0.3;
      if (sEmbPath)   sEmbPath.value   = data.embedding_model_path || '';
      if (sEmbModel)  sEmbModel.value  = data.embedding_model  || '';
      if (sEmbSsl)    sEmbSsl.checked  = data.embedding_ssl_verify !== false;
      if (sHfToken)   sHfToken.value   = data.hf_token         || '';
    } catch (err) {
      showToast('Ошибка загрузки настроек: ' + err.message, 'error');
    }
    // Load last sync timestamp from sync status endpoint
    try {
      const syncData = await api.syncStatus();
      const ts = syncData?.last_sync_at || '';
      if (sLastSyncAt) sLastSyncAt.textContent = ts ? `${ts} (МСК)` : 'Ещё не выполнялась';
      if (sLastSyncRow) sLastSyncRow.style.display = '';
    } catch {
      // Non-critical: sync status may be unavailable if server is starting up
    }
  }

  sSaveBtn?.addEventListener('click', async () => {
    try {
      await api.settingsSave({
        mlx_model_path:       sMlxModel?.value        || '',
        vault_path:           sVaultPath?.value        || '',
        mlx_max_tokens:       parseInt(sMaxTokens?.value || '1024', 10),
        mlx_temperature:      parseFloat(sTemp?.value  || '0.3'),
        embedding_model_path: sEmbPath?.value          || '',
        embedding_model:      sEmbModel?.value         || '',
        embedding_ssl_verify: sEmbSsl?.checked ?? true,
        hf_token:             sHfToken?.value          || '',
      });
      if (sStatus) { sStatus.textContent = 'Сохранено ✓'; setTimeout(() => { sStatus.textContent = ''; }, 2500); }
      showToast('Настройки сохранены. Перезапустите сервер для применения.', 'success');
      // Refresh vault diagnostics (vault path may have changed)
      await checkVault();
    } catch (err) {
      showToast('Ошибка сохранения: ' + err.message, 'error');
    }
  });

  // ── Vault diagnostics ─────────────────────────────────────────────────────
  const vaultDiagBanner  = document.getElementById('vault-diag-banner');
  const vaultDiagIcon    = document.getElementById('vault-diag-icon');
  const vaultDiagTitle   = document.getElementById('vault-diag-title');
  const vaultDiagDetails = document.getElementById('vault-diag-details');
  const vaultDiagReload  = document.getElementById('vault-diag-reload');

  function showDiag(diag) {
    if (!vaultDiagBanner) return;
    vaultDiagBanner.style.display = '';

    if (!diag.vault_exists) {
      vaultDiagBanner.style.background = 'var(--color-bg-warning, #fff7ed)';
      vaultDiagBanner.style.borderColor = 'var(--warning, #f59e0b)';
      if (vaultDiagIcon)  vaultDiagIcon.textContent  = '⚠️';
      if (vaultDiagTitle) vaultDiagTitle.textContent = 'Vault не найден';
      if (vaultDiagDetails) vaultDiagDetails.textContent =
        `Путь «${diag.vault_path}» не существует. Проверьте настройку «Путь к Vault» и перезапустите сервер.`;
    } else if (!diag.index_loaded || diag.md_count === 0) {
      vaultDiagBanner.style.background = 'var(--color-bg-warning, #fff7ed)';
      vaultDiagBanner.style.borderColor = 'var(--warning, #f59e0b)';
      if (vaultDiagIcon)  vaultDiagIcon.textContent  = '⚠️';
      if (vaultDiagTitle) vaultDiagTitle.textContent = diag.md_count === 0
        ? 'Vault пуст — нет .md файлов'
        : 'Индекс не загружен';
      const hint = diag.md_count === 0
        ? 'Запустите «Синхронизировать» во вкладке «Синхронизация» или создайте тестовые данные во вкладке 🧪 Данные (dev).'
        : 'Нажмите «↻ Перезагрузить vault» во вкладке «Синхронизация».';
      if (vaultDiagDetails) vaultDiagDetails.textContent =
        `Vault: ${diag.vault_path} • Файлов: ${diag.md_count}. ${hint}`;
    } else {
      vaultDiagBanner.style.background = 'var(--color-bg-success, #f0fdf4)';
      vaultDiagBanner.style.borderColor = 'var(--success, #22c55e)';
      if (vaultDiagIcon)  vaultDiagIcon.textContent  = '✅';
      if (vaultDiagTitle) vaultDiagTitle.textContent = 'Vault в порядке';
      const secInfo = Object.entries(diag.sections || {})
        .map(([s, n]) => `${s}: ${n}`).join(', ');
      if (vaultDiagDetails) vaultDiagDetails.textContent =
        `${diag.md_count} файлов • Индекс: ${diag.index_doc_count} документов${secInfo ? ' • ' + secInfo : ''}`;
    }
  }

  async function checkVault() {
    if (vaultDiagBanner) {
      vaultDiagBanner.style.display = '';
      vaultDiagBanner.style.background = 'var(--color-bg)';
      vaultDiagBanner.style.borderColor = 'var(--color-border)';
      if (vaultDiagIcon)  vaultDiagIcon.textContent  = '⏳';
      if (vaultDiagTitle) vaultDiagTitle.textContent = 'Проверка vault…';
      if (vaultDiagDetails) vaultDiagDetails.textContent = '';
    }
    try {
      const diag = await api.vaultDiagnostics();
      showDiag(diag);
    } catch (err) {
      if (vaultDiagBanner) vaultDiagBanner.style.display = '';
      if (vaultDiagIcon)  vaultDiagIcon.textContent  = '❌';
      if (vaultDiagTitle) vaultDiagTitle.textContent = 'Ошибка диагностики';
      if (vaultDiagDetails) vaultDiagDetails.textContent = err.message;
    }
  }

  sCheckVault?.addEventListener('click', checkVault);
  vaultDiagReload?.addEventListener('click', async () => {
    try {
      await api.vaultReload();
      await checkVault();
      showToast('Vault перезагружен', 'success');
    } catch (err) {
      showToast('Ошибка: ' + err.message, 'error');
    }
  });

  // ── Souls.md ──────────────────────────────────────────────────────────────
  const soulsEditor = document.getElementById('souls-editor');
  const soulsSave   = document.getElementById('souls-save');
  const soulsStatus = document.getElementById('souls-status');
  let soulsLoaded   = false;

  async function loadSouls() {
    if (soulsLoaded) return;
    try {
      const data = await api.soulsGet();
      if (soulsEditor) soulsEditor.value = data.content || '';
      soulsLoaded = true;
    } catch (err) {
      showToast('Ошибка загрузки souls.md: ' + err.message, 'error');
    }
  }

  soulsSave?.addEventListener('click', async () => {
    try {
      await api.soulsSave(soulsEditor?.value || '');
      if (soulsStatus) { soulsStatus.textContent = 'Сохранено ✓'; setTimeout(() => { soulsStatus.textContent = ''; }, 2000); }
      showToast('souls.md сохранён', 'success');
    } catch (err) {
      showToast('Ошибка: ' + err.message, 'error');
    }
  });

  // ── Profile & Assistant Config (new v2 API) ───────────────────────────────
  const pUserName       = document.getElementById('p-user-name');
  const pUserRole       = document.getElementById('p-user-role');
  const pUserLang       = document.getElementById('p-user-language');
  const pAsstName       = document.getElementById('p-assistant-name');
  const pAsstStyle      = document.getElementById('p-assistant-style');
  const pAsstFocus      = document.getElementById('p-assistant-focus');
  const personaSaveBtn  = document.getElementById('persona-save');
  const personaStatus   = document.getElementById('persona-status');
  let personaLoaded     = false;

  async function loadPersona() {
    if (personaLoaded) return;
    try {
      const [profile, config] = await Promise.all([
        api.profileGet().catch(() => ({})),
        api.assistantConfigGet().catch(() => ({})),
      ]);
      if (pUserName)  pUserName.value   = profile.full_name       || '';
      if (pUserRole)  pUserRole.value   = (profile.context_notes || '').replace(/^Role: /, '') || '';
      if (pUserLang)  pUserLang.value   = profile.preferred_language || 'ru';
      if (pAsstName)  pAsstName.value   = config.name             || 'Ассистент';
      if (pAsstStyle) pAsstStyle.value  = config.tone_style       || 'professional';
      if (pAsstFocus) pAsstFocus.value  = config.system_prompt_template || '';
      personaLoaded = true;
    } catch (err) {
      showToast('Ошибка загрузки профиля: ' + err.message, 'error');
    }
  }

  personaSaveBtn?.addEventListener('click', async () => {
    try {
      const roleNote = pUserRole?.value ? `Role: ${pUserRole.value}` : '';
      await api.profileSave({
        full_name: pUserName?.value || '',
        preferred_language: pUserLang?.value || 'ru',
        communication_tone: pAsstStyle?.value || 'professional',
        timezone: 'Europe/Moscow',
        context_notes: roleNote || null,
      });
      await api.assistantConfigSave({
        name: pAsstName?.value || 'Ассистент',
        response_language: pUserLang?.value || 'ru',
        tone_style: pAsstStyle?.value || 'professional',
        system_prompt_template: pAsstFocus?.value || '',
        max_context_tokens: 12000,
      });
      if (personaStatus) { personaStatus.textContent = 'Сохранено ✓'; setTimeout(() => { personaStatus.textContent = ''; }, 2000); }
      showToast('Профиль и конфигурация сохранены', 'success');
      // Trigger context rebuild in chat
      document.dispatchEvent(new CustomEvent('pa:profile-updated'));
    } catch (err) {
      showToast('Ошибка: ' + err.message, 'error');
    }
  });

  // NOTE: Classify, Tool Prompts, and Tools sections have been moved to the
  // Rules tab (rules.js / initToolsTab). Settings.js no longer manages them.

  // ── Sync ──────────────────────────────────────────────────────────────────
  const syncStartBtn    = document.getElementById('sync-start-btn');
  const indexBuildBtn   = document.getElementById('index-build-btn');
  const vaultReloadBtn  = document.getElementById('vault-reload-settings-btn');
  const progressWrap    = document.getElementById('sync-progress-wrap');
  const progressFill    = document.getElementById('sync-progress-fill');
  const progressLabel   = document.getElementById('sync-progress-label');
  const progressPct     = document.getElementById('sync-progress-pct');
  const syncLog         = document.getElementById('sync-log');

  let syncPollTimer = null;
  let _syncRunning = false;

  function addLog(msg) {
    if (!syncLog) return;
    const line = document.createElement('div');
    line.textContent = `[${new Date().toLocaleTimeString()}] ${msg}`;
    syncLog.appendChild(line);
    syncLog.scrollTop = syncLog.scrollHeight;
  }

  function setProgress(pct, label) {
    if (progressWrap) progressWrap.style.display = '';
    if (progressFill) progressFill.style.width = pct + '%';
    if (progressLabel) progressLabel.textContent = label || 'Синхронизация…';
    if (progressPct) progressPct.textContent = pct + '%';
  }

  function hideProgress() {
    if (progressWrap) progressWrap.style.display = 'none';
  }

  // Track last logged message to avoid duplicates in the log panel
  let _lastLoggedMsg = '';

  async function pollSyncStatus() {
    try {
      const s = await api.syncStatus();
      setProgress(Math.round(s.pct || 0), s.message || 'Синхронизация…');
      // Avoid flooding the log with the same message on every poll tick
      if (s.message && s.message !== _lastLoggedMsg) {
        _lastLoggedMsg = s.message;
        addLog(s.message);
      }
      if (!s.running) {
        clearInterval(syncPollTimer);
        syncPollTimer = null;
        _syncRunning = false;
        syncStartBtn.disabled = false;
        hideProgress();
        if (s.stage === 'error') {
          showToast('Ошибка синхронизации: ' + (s.error || '?'), 'error');
          addLog('❌ ' + (s.error || 'Ошибка'));
        } else {
          const hasWarnings = s.warnings && s.warnings.length > 0;
          showToast(s.message || 'Синхронизация завершена', hasWarnings ? 'error' : 'success');
          addLog((hasWarnings ? '⚠️' : '✅') + ' ' + (s.message || 'Синхронизация завершена'));
          // Show each per-source warning as a separate toast so they're visible
          if (hasWarnings) {
            for (const w of s.warnings) {
              addLog('  ⚠️ ' + w);
              showToast(w, 'error');
            }
          }
          // Auto-refresh vault list, tags, diagnostics
          document.dispatchEvent(new CustomEvent('pa:vault-reloaded'));
          await checkVault();
        }
      }
    } catch {
      clearInterval(syncPollTimer);
      syncPollTimer = null;
      _syncRunning = false;
      syncStartBtn.disabled = false;
      hideProgress();
    }
  }

  syncStartBtn?.addEventListener('click', async () => {
    if (_syncRunning) return;
    try {
      const sources = [];
      if (document.getElementById('s-sync-calendar')?.checked) sources.push('calendar');
      if (document.getElementById('s-sync-mail')?.checked)     sources.push('mail');
      if (!sources.length) {
        showToast('Выберите хотя бы один источник', 'error');
        return;
      }
      addLog('Запуск синхронизации: ' + sources.join(', ') + '…');
      _syncRunning = true;
      syncStartBtn.disabled = true;
      await api.syncStart({ sources });
      setProgress(0, 'Запуск…');
      clearInterval(syncPollTimer);
      syncPollTimer = setInterval(pollSyncStatus, 1500);
    } catch (err) {
      _syncRunning = false;
      syncStartBtn.disabled = false;
      showToast('Ошибка синхронизации: ' + err.message, 'error');
    }
  });

  const syncOpProgress = new OperationProgress(
    document.getElementById('sync-op-progress') || document.createElement('div')
  );

  indexBuildBtn?.addEventListener('click', async () => {
    indexBuildBtn.disabled = true;
    syncOpProgress.start('Построение векторного индекса…');
    addLog('Построение векторного индекса…');
    try {
      await api.indexBuild();
      syncOpProgress.finish('Индекс построен ✓');
      addLog('Индекс построен');
      showToast('Индекс построен', 'success');
    } catch (err) {
      syncOpProgress.finish('Ошибка индексации: ' + err.message, true);
      showToast('Ошибка: ' + err.message, 'error');
    } finally {
      indexBuildBtn.disabled = false;
    }
  });

  vaultReloadBtn?.addEventListener('click', async () => {
    vaultReloadBtn.disabled = true;
    syncOpProgress.start('Перезагрузка vault index…');
    addLog('Перезагрузка vault…');
    try {
      await api.vaultReload();
      syncOpProgress.finish('Vault перезагружен ✓');
      addLog('Vault перезагружен');
      showToast('Vault перезагружен', 'success');
      // Auto-refresh vault list + search
      document.dispatchEvent(new CustomEvent('pa:vault-reloaded'));
      // Refresh diagnostics banner
      await checkVault();
    } catch (err) {
      syncOpProgress.finish('Ошибка: ' + err.message, true);
      showToast('Ошибка: ' + err.message, 'error');
    } finally {
      vaultReloadBtn.disabled = false;
    }
  });

  // ── Classify-apply from Settings tab ─────────────────────────────────────
  const classifyApplySettingsBtn = document.getElementById('classify-apply-settings-btn');
  classifyApplySettingsBtn?.addEventListener('click', async () => {
    classifyApplySettingsBtn.disabled = true;
    syncOpProgress.start('Применение классификации…');
    addLog('Запуск классификации vault…');
    try {
      const res = await api.classifyApply();
      const msg = `Классифицировано: ${res.classified ?? 0}, ошибок: ${res.errors ?? 0}`;
      syncOpProgress.finish(msg + ' ✓');
      addLog('✅ ' + msg);
      showToast(msg, 'success');
    } catch (err) {
      syncOpProgress.finish('Ошибка классификации: ' + err.message, true);
      showToast('Ошибка: ' + err.message, 'error');
    } finally {
      classifyApplySettingsBtn.disabled = false;
    }
  });

  // ── Individual source sync buttons ───────────────────────────────────────
  // Each button syncs only its own source (calendar / mail)
  document.querySelectorAll('.sync-source-btn').forEach(btn => {
    btn.addEventListener('click', async () => {
      if (_syncRunning) { showToast('Синхронизация уже выполняется', 'error'); return; }
      const source = btn.dataset.source;
      if (!source) return;

      const label = { calendar: '📅 Календарь', mail: '📧 Почта' }[source] || source;
      addLog(`Запуск синхронизации: ${label}…`);

      // Disable all source buttons + start-all during this run
      document.querySelectorAll('.sync-source-btn, #sync-start-btn').forEach(b => { b.disabled = true; });
      _syncRunning = true;

      try {
        await api.syncStart({ sources: [source] });
        setProgress(0, `Синхронизация ${label}…`);
        clearInterval(syncPollTimer);
        syncPollTimer = setInterval(pollSyncStatus, 1500);
      } catch (err) {
        _syncRunning = false;
        document.querySelectorAll('.sync-source-btn, #sync-start-btn').forEach(b => { b.disabled = false; });
        showToast('Ошибка: ' + err.message, 'error');
      }
    });
  });

  // Re-enable source buttons when sync finishes (patch pollSyncStatus to also re-enable them)
  const _origPollSyncStatus = pollSyncStatus;
  // We already handle re-enable inside pollSyncStatus via syncStartBtn.disabled = false,
  // but source buttons need the same treatment — add a MutationObserver on syncStartBtn
  const _syncBtnObserver = new MutationObserver(() => {
    const isDisabled = syncStartBtn?.disabled;
    document.querySelectorAll('.sync-source-btn').forEach(b => { b.disabled = !!isDisabled; });
  });
  if (syncStartBtn) _syncBtnObserver.observe(syncStartBtn, { attributes: true, attributeFilter: ['disabled'] });

  // ── Schedule settings ─────────────────────────────────────────────────────
  const scheduleEnabledCb  = document.getElementById('s-schedule-enabled');
  const scheduleCronInput  = document.getElementById('s-schedule-cron');
  const scheduleCronHint   = document.getElementById('schedule-cron-hint');
  const scheduleNextRow    = document.getElementById('schedule-next-row');
  const scheduleNextRun    = document.getElementById('schedule-next-run');
  const scheduleSaveBtn    = document.getElementById('schedule-save-btn');
  const scheduleRunNowBtn  = document.getElementById('schedule-run-now-btn');

  // Human-readable cron description (simple, no lib)
  function describeCron(cron) {
    const presets = {
      '0 9 * * *':     'Ежедневно в 09:00 UTC',
      '0 9 * * 1-5':   'По будням в 09:00 UTC',
      '0 */6 * * *':   'Каждые 6 часов',
      '0 * * * *':     'Каждый час',
      '*/30 * * * *':  'Каждые 30 минут',
      '0 0 * * *':     'Ежедневно в полночь UTC',
    };
    return presets[cron?.trim()] || (cron?.trim() ? `cron: ${cron.trim()}` : '—');
  }

  function updateCronHint() {
    if (scheduleCronHint) scheduleCronHint.textContent = describeCron(scheduleCronInput?.value);
  }

  scheduleCronInput?.addEventListener('input', updateCronHint);

  // Preset buttons
  document.querySelectorAll('.schedule-preset-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      if (scheduleCronInput) { scheduleCronInput.value = btn.dataset.cron; updateCronHint(); }
    });
  });

  async function loadScheduleStatus() {
    try {
      const s = await api.scheduleStatus();
      if (scheduleEnabledCb) scheduleEnabledCb.checked = !!s.enabled;
      if (scheduleCronInput) { scheduleCronInput.value = s.cron || '0 9 * * *'; updateCronHint(); }
      if (s.next_run && scheduleNextRun) {
        const d = new Date(s.next_run);
        scheduleNextRun.textContent = d.toLocaleString('ru-RU', { dateStyle: 'short', timeStyle: 'short' }) + ' UTC';
        if (scheduleNextRow) scheduleNextRow.style.display = '';
      } else if (scheduleNextRow) {
        scheduleNextRow.style.display = s.enabled ? '' : 'none';
        if (scheduleNextRun) scheduleNextRun.textContent = s.enabled ? 'Неизвестно' : 'Расписание отключено';
      }
    } catch { /* server may be offline */ }
  }

  scheduleSaveBtn?.addEventListener('click', async () => {
    scheduleSaveBtn.disabled = true;
    const enabled = scheduleEnabledCb?.checked ?? false;
    const cron    = scheduleCronInput?.value?.trim() || '0 9 * * *';
    try {
      await api.settingsSave({ schedule_enabled: enabled, schedule_cron: cron });
      showToast('Расписание сохранено. Перезапустите сервер для применения.', 'success');
      addLog(`✅ Расписание: ${enabled ? cron : 'отключено'}`);
      await loadScheduleStatus();
    } catch (err) {
      showToast('Ошибка сохранения: ' + err.message, 'error');
    } finally {
      scheduleSaveBtn.disabled = false;
    }
  });

  scheduleRunNowBtn?.addEventListener('click', async () => {
    scheduleRunNowBtn.disabled = true;
    addLog('Запуск pipeline вручную…');
    try {
      await api.syncStart({ sources: ['calendar', 'mail'] });
      setProgress(0, 'Запуск pipeline…');
      clearInterval(syncPollTimer);
      _syncRunning = true;
      syncStartBtn.disabled = true;
      syncPollTimer = setInterval(pollSyncStatus, 1500);
      showToast('Pipeline запущен', 'success');
    } catch (err) {
      showToast('Ошибка: ' + err.message, 'error');
    } finally {
      scheduleRunNowBtn.disabled = false;
    }
  });

  // Load schedule on tab open
  loadScheduleStatus();

  // ── Test-data generator ───────────────────────────────────────────────────
  const tdGenerateBtn   = document.getElementById('td-generate-btn');
  const tdDeleteBtn     = document.getElementById('td-delete-btn');
  const tdRefreshSnaps  = document.getElementById('td-refresh-snaps');
  const tdResult        = document.getElementById('td-result');
  const tdSnapsList     = document.getElementById('td-snapshots-list');

  function tdLog(html) {
    if (!tdResult) return;
    tdResult.style.display = '';
    tdResult.innerHTML = html;
  }

  async function loadSnapshots() {
    if (!tdSnapsList) return;
    tdSnapsList.innerHTML = '<div style="color:var(--color-text-muted);font-size:13px;padding:8px 0">Загрузка…</div>';
    try {
      const data = await api.getSnapshots();
      if (!data.snapshots.length) {
        tdSnapsList.innerHTML = '<div style="color:var(--color-text-muted);font-size:13px;padding:8px 0">Снапшотов нет</div>';
        return;
      }
      tdSnapsList.innerHTML = '';
      for (const snap of data.snapshots) {
        const row = document.createElement('div');
        row.style.cssText = 'display:flex;align-items:center;gap:10px;padding:8px 12px;background:var(--color-bg);border:1px solid var(--color-border);border-radius:6px;margin-bottom:6px;font-size:13px';

        const created = new Date(snap.created).toLocaleString('ru-RU', { dateStyle: 'short', timeStyle: 'short' });
        const label = document.createElement('span');
        label.style.flex = '1';
        label.innerHTML = `<span style="font-weight:500;color:var(--color-text)">${snap.id}</span> <span style="color:var(--color-text-muted)">${created} · ${snap.size_kb} KB</span>`;

        const rollbackBtn = document.createElement('button');
        rollbackBtn.className = 'btn btn--sm';
        rollbackBtn.innerHTML = '↩ Откатить';
        rollbackBtn.title = 'Восстановить vault из этого снапшота';
        rollbackBtn.addEventListener('click', async () => {
          if (!confirm(`Откатить vault к снапшоту "${snap.id}"?\nТестовые данные будут удалены, vault восстановлен.`)) return;
          rollbackBtn.disabled = true;
          rollbackBtn.textContent = '…';
          try {
            const r = await api.rollbackSnapshot(snap.id);
            if (r.ok) {
              showToast('Vault откатан к снапшоту ✓', 'success');
              tdLog(`✅ Восстановлено из: <b>${snap.id}</b>`);
            }
          } catch (err) {
            showToast('Ошибка отката: ' + err.message, 'error');
            rollbackBtn.textContent = '↩ Откатить';
          } finally {
            rollbackBtn.disabled = false;
            loadSnapshots();
          }
        });

        const delBtn = document.createElement('button');
        delBtn.className = 'btn btn--sm btn--secondary';
        delBtn.textContent = '🗑';
        delBtn.title = 'Удалить снапшот';
        delBtn.addEventListener('click', async () => {
          if (!confirm(`Удалить снапшот "${snap.id}"?`)) return;
          try {
            await api.deleteSnapshot(snap.id);
            loadSnapshots();
          } catch (err) {
            showToast('Ошибка: ' + err.message, 'error');
          }
        });

        row.appendChild(label);
        row.appendChild(rollbackBtn);
        row.appendChild(delBtn);
        tdSnapsList.appendChild(row);
      }
    } catch (err) {
      tdSnapsList.innerHTML = `<div style="color:var(--danger);font-size:13px">Ошибка: ${err.message}</div>`;
    }
  }

  tdGenerateBtn?.addEventListener('click', async () => {
    const events   = document.getElementById('td-events')?.checked   ?? true;
    const mail     = document.getElementById('td-mail')?.checked     ?? true;
    const projects = document.getElementById('td-projects')?.checked ?? true;
    const snapshot = document.getElementById('td-snapshot')?.checked ?? true;

    if (!events && !mail && !projects) {
      showToast('Выберите хотя бы один тип данных', 'error');
      return;
    }

    tdGenerateBtn.disabled = true;
    tdGenerateBtn.innerHTML = '⏳ Генерация…';
    tdLog('⏳ Генерирую тестовые данные…');

    try {
      const r = await api.generateTestData({ events, mail, projects, snapshot });
      const lines = [
        r.ok ? '✅ Данные сгенерированы' : '⚠️ Завершено с ошибками',
        r.snap_id ? `💾 Снапшот: <b>${r.snap_id}</b>` : '',
        r.events_count ? `📅 Встречи: ${r.events_count} шт.` : '',
        r.mail_count   ? `📧 Письма: ${r.mail_count} шт.`   : '',
        r.proj_count   ? `📋 Проекты: ${r.proj_count} шт.`  : '',
        r.created.length ? `<br>Файлы:<br>` + r.created.map(f => `&nbsp;&nbsp;· ${f}`).join('<br>') : '',
        r.errors.length ? `<br><span style="color:var(--danger)">Ошибки:<br>` + r.errors.join('<br>') + '</span>' : '',
      ].filter(Boolean).join('<br>');
      tdLog(lines);

      if (r.ok) showToast(`Сгенерировано: ${r.events_count + r.mail_count} файлов, ${r.proj_count} проектов`, 'success');
      else      showToast('Генерация завершена с ошибками', 'error');

      loadSnapshots();
    } catch (err) {
      tdLog(`❌ Ошибка: ${err.message}`);
      showToast('Ошибка генерации: ' + err.message, 'error');
    } finally {
      tdGenerateBtn.disabled = false;
      tdGenerateBtn.innerHTML = `<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" width="14" height="14"><path stroke-linecap="round" stroke-linejoin="round" d="M9.813 15.904L9 18.75l-.813-2.846a4.5 4.5 0 00-3.09-3.09L2.25 12l2.846-.813a4.5 4.5 0 003.09-3.09L9 5.25l.813 2.846a4.5 4.5 0 003.09 3.09L15.75 12l-2.846.813a4.5 4.5 0 00-3.09 3.09z"/></svg> Сгенерировать данные`;
    }
  });

  tdDeleteBtn?.addEventListener('click', async () => {
    if (!confirm('Удалить все тестовые данные из vault и проектов?\n(Снапшоты останутся для отката)')) return;
    tdDeleteBtn.disabled = true;
    try {
      const r = await api.deleteGeneratedData();
      tdLog(`🗑 Удалено: файлов — ${r.removed_files.length}, проектов — ${r.removed_projects}`);
      showToast('Тестовые данные удалены', 'success');
    } catch (err) {
      showToast('Ошибка: ' + err.message, 'error');
    } finally {
      tdDeleteBtn.disabled = false;
    }
  });

  tdRefreshSnaps?.addEventListener('click', loadSnapshots);

  // ── Init ──────────────────────────────────────────────────────────────────
  loadGeneral();
  // Run vault check automatically when settings tab opens
  checkVault();
}
