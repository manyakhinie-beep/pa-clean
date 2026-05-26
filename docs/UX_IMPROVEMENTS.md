# UX Improvements — personal-assistant (pa-merge)
**Date:** 2026-05-24  
**Scope:** Improvements applied in the QA & UX Audit pass  

---

## Summary

This document describes all UX improvements applied during the audit. Every change is backward-compatible, UI/UX-only (no business logic modified), and annotated with `// UX-FIX:` or `// BUG-FIX:` comments in the source.

---

## Improvement 1 — Critical: Restore 13 Missing CSS Design Tokens

**ID:** CSS-1  
**Files changed:** `webui/frontend/styles/variables.scss`  
**Comment tag:** `// UX-FIX CSS-1`

### What broke
After the design-system migration from `webui/scss/` to `webui/frontend/styles/`, 13 CSS custom properties that component SCSS files reference were not ported to the new `:root {}` block. Browsers silently render undefined CSS variables as `transparent` or `inherit`, causing invisible hover states, missing accent colours, and wrong secondary text colour across the app.

### What changed
Added all 13 missing tokens to `:root {}` with semantically correct Sber brandbook values, and added dark-mode counterparts:

```scss
// Light mode (added to :root {})
--color-primary:         #21A038;
--color-primary-rgb:     33, 160, 56;
--color-accent:          #21A038;
--color-accent-subtle:   #E8F5EC;
--color-hover:           #F3F4F6;      // subtle row/card hover bg
--color-bg-hover:        #F3F4F6;
--color-bg-subtle:       #F9FAFB;
--color-bg-sidebar:      #FFFFFF;
--color-text-secondary:  #4B5563;      // slightly stronger than --color-text-muted
--color-warn:            #FFA000;
--color-surface-alt:     #F9FAFB;
--color-surface-raised:  #FFFFFF;
--primary-dark:          #1B8A30;

// Dark mode (added to [data-theme="dark"] {})
--color-hover:           #374151;
--color-bg-hover:        #374151;
--color-bg-subtle:       #1A2233;
--color-bg-sidebar:      #1A2233;
--color-text-secondary:  #D1D5DB;
--color-surface-alt:     #1A2233;
--color-surface-raised:  #2D3748;
```

### User-visible impact
- Hover backgrounds on inbox items, vault cards, and rule rows now render correctly (was: invisible)
- Secondary text in metadata, timestamps, and labels now shows correct grey instead of inheriting parent colour
- Accent colour (Sber green) correctly applied in all components that used `--color-primary` / `--color-accent`
- Dark mode now fully supported for all new components

---

## Improvement 2 — Accessibility: Screen Reader Toast Announcements

**ID:** A11Y-1  
**Files changed:** `webui/index.html`  
**Comment tag:** `<!-- BUG-FIX A11Y-1 -->`

### What changed
Added `role="status"` and `aria-live="polite"` to `#toast-container`:

```html
<div id="toast-container"
     role="status"
     aria-live="polite"
     aria-atomic="false"
     style="position:fixed;bottom:24px;right:24px;...">
</div>
```

### User-visible impact
Screen reader users (VoiceOver, NVDA, JAWS) now hear toast notifications: "Синхронизация запущена", "Ошибка подключения к MLX", "Черновик сохранён". Previously these messages were visually present but completely inaccessible to non-sighted users.

`aria-atomic="false"` ensures each individual toast is announced rather than the entire container being re-read.

---

## Improvement 3 — Accessibility: Aria Labels on Icon Buttons

**ID:** A11Y-2  
**Files changed:** `webui/index.html`  
**Comment tag:** `<!-- BUG-FIX A11Y-2 -->`

### What changed
Added explicit `aria-label` to 14 icon-only buttons across Today, Chat, Vault, and Projects sections. All labels match the existing `title` text (which already described the action correctly).

### Why `aria-label` is needed alongside `title`
The `title` attribute provides a tooltip on hover, but:
- It is not reliably exposed as the accessible name by all browser+screen-reader combinations
- It is unavailable to keyboard-only users who don't hover
- WCAG 2.1 Success Criterion 4.1.2 requires all UI components to have an accessible name

### Buttons improved

**Today section:**
- `#today-brief-refresh` → "Обновить из кэша"
- `#today-brief-regen` → "Пересоздать брифинг с помощью ИИ"
- `#today-brief-ask` → "Спросить ассистента о дне"
- `#today-events-nav` → "Открыть календарь"
- `#today-attention-nav` → "Открыть Inbox"

**Chat section:**
- `#chat-new-thread` → "Новый чат"
- `#chat-clear-thread` → "Очистить историю"
- `#chat-delete-thread` → "Удалить тред"
- `#chat-clear-all-threads` → "Очистить все треды"
- `#chat-at-btn` → "Упомянуть документ из Vault"
- `#chat-slash-btn` → "Slash-команды"

**Vault section:**
- `#vault-reload-btn` → "Обновить vault"

**Projects section:**
- `#projects-detail-edit` → "Редактировать проект"
- `#projects-detail-delete` → "Удалить проект"

---

## Improvement 4 — Design Consistency: Danger Outline Button Variant

**ID:** CSS-2  
**Files changed:** `webui/index.html`, `webui/frontend/styles/main.scss`  
**Comment tag:** `// UX-FIX CSS-2`

### What changed
Replaced inline `style="border-color:#ef4444;color:#ef4444"` on `#classify-reset-btn` with a proper CSS modifier class:

**HTML:**
```html
<!-- Before -->
<button class="btn btn--secondary" id="classify-reset-btn" style="border-color:#ef4444;color:#ef4444">

<!-- After -->
<button class="btn btn--secondary btn--danger-outline" id="classify-reset-btn">
```

**SCSS:**
```scss
&--danger-outline {
  background: transparent;
  color: var(--danger);         // uses design token, not hardcoded hex
  border-color: var(--danger);
  &:hover { background: rgba(229, 57, 53, .06); }
}
```

### Benefits
- Uses `--danger` design token (`#E53935`) — matches the rest of the app's danger colour, was previously a slightly different shade (`#ef4444`)
- Dark mode compatible (token is overridable; inline style was not)
- Can be reused on any future "destructive outline" button
- Cannot be accidentally overridden by specificity issues

---

## Improvement 5 — Accessibility: Keyboard Focus Rings

**ID:** CSS-3  
**Files changed:**
- `webui/frontend/styles/components/_nav.scss`
- `webui/frontend/styles/components/_projects.scss`
- `webui/frontend/styles/main.scss`  
**Comment tag:** `// UX-FIX CSS-3`

### What changed
Added `:focus-visible` outlines to:
1. `.nav__item` (sidebar navigation links)
2. `.nav__footer-btn` (Settings button at bottom of nav)
3. `.projects__filter-tab` (Projects status filter tabs)
4. `.projects__list-item` (individual project rows)
5. All `.btn` elements globally (via `main.scss`)

```scss
&:focus-visible {
  outline: 2px solid var(--primary);  // Sber green, 2px
  outline-offset: -2px;               // inset so it doesn't shift layout
}
```

### Why `:focus-visible` (not `:focus`)
`:focus-visible` is only applied when the browser determines keyboard navigation is in use. Mouse clicks do not trigger the ring, so the visual change is non-intrusive for mouse users. This follows WCAG 2.1 SC 2.4.7 (Focus Visible) without degrading the mouse experience.

### User-visible impact
Tab-key navigation through the sidebar and projects panel now shows a clear 2px green focus ring on the active element. Previously, keyboard users had no visual indicator of which element was focused.

---

## Files Modified (Summary)

| File | Change Type | Lines Changed |
|---|---|---|
| `webui/frontend/styles/variables.scss` | `:root {}` + `[data-theme="dark"] {}` additions | +28 lines |
| `webui/frontend/styles/main.scss` | `.btn--danger-outline` + `.btn:focus-visible` | +18 lines |
| `webui/frontend/styles/components/_nav.scss` | `:focus-visible` on items + footer btn | +8 lines |
| `webui/frontend/styles/components/_projects.scss` | `:focus-visible` on tabs + list items | +10 lines |
| `webui/index.html` | `aria-live` on toast, `aria-label` on 14 buttons, class on reset btn | +18 attrs |
| `webui/dist/css/main.css` | Recompiled from SCSS | recompiled |
| `webui/dist/index.html` | Synced from source | synced |

---

## Verification

```bash
# SCSS compilation: 0 errors, 0 warnings
npx sass webui/frontend/styles/main.scss webui/dist/css/main.css

# Tests: 948 passed, 0 failed
python -m pytest tests/ --no-cov
# → 948 passed, 3 skipped in 2.82s
```

All changes are backward-compatible. No JavaScript logic was modified. No API contracts were changed. The CSS token additions only add new variables — they do not override existing ones.

---

## Future Recommendations (Not Implemented)

These are UX improvements identified during the audit that are worth considering in future sprints, but were deliberately not implemented to stay within the "UI/UX bugs only" scope:

1. **Add text-search input to Inbox** — currently filtering is tab-only. A debounced (`≥300ms`) text search would improve usability for large mailboxes.
2. **Vault list keyboard navigation** — arrow keys to move between items (like Inbox already has).
3. **Reduce motion media query** — animated toasts could respect `@media (prefers-reduced-motion)`.
4. **WCAG contrast audit on muted text** — `--color-text-faint` (`#9CA3AF` on white) is 2.85:1, below WCAG AA 4.5:1 for normal text. Acceptable for placeholder/label text only.
5. **Error boundary in chat streaming** — currently a streaming error shows no user-visible fallback message in the chat bubble.
