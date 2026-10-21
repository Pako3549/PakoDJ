# GUI Style

## Tech Stack
- **Tailwind CSS** v3 (CDN) with custom config
- **Dark mode** only (`class="dark"` on `<html>`)
- **SSE** (EventSource) for real-time updates
- **FastAPI** backend serving static HTML + JSON endpoints

## Theme System
- CSS custom properties (`:root` for light, `.dark` overrides)
- Toggle via button with localStorage persistence
- Default: respects `prefers-color-scheme`, falls back to dark
- Smooth 0.3s transition on `background-color` and `color`

### Light (`:root`)
| Token | Value |
|-------|-------|
| `--color-surface` | `#f3f4f6` |
| `--color-card` | `#ffffff` |
| `--color-border` | `#d1d5db` |
| `--color-text-primary` | `#111827` |
| `--color-text-secondary` | `#4b5563` |
| `--color-text-muted` | `#9ca3af` |
| `--color-text-inverse` | `#ffffff` |
| `--color-msg-in` | `#e8f5e9` |
| `--color-msg-out` | `#e3f2fd` |
| `--scrollbar-thumb` | `#d1d5db` |

### Dark (`.dark`)
| Token | Value |
|-------|-------|
| `--color-surface` | `#111318` |
| `--color-card` | `#1a1d23` |
| `--color-border` | `#2a2d35` |
| `--color-text-primary` | `#f3f4f6` |
| `--color-text-secondary` | `#9ca3af` |
| `--color-text-muted` | `#6b7280` |
| `--color-text-inverse` | `#000000` |
| `--color-msg-in` | `#1a2e1a` |
| `--color-msg-out` | `#1a2744` |
| `--scrollbar-thumb` | `#2a2d35` |

## Color Tokens (Tailwind)
| Token | CSS Variable | Usage |
|-------|-------------|-------|
| `surface` | `--color-surface` | Page background |
| `card` | `--color-card` | Card/section background |
| `border` | `--color-border` | Borders, separators |
| `accent` | `#25D366` | Green — labels, start button, status |
| `accent2` | `#00a8ff` | Blue — outgoing message labels |
| `primary` | `--color-text-primary` | Main text, headings, values |
| `secondary` | `--color-text-secondary` | Log entries, secondary text |
| `muted` | `--color-text-muted` | Labels, empty states, timestamps |
| `inverse` | `--color-text-inverse` | Button text (contrast with accent) |

Standard Tailwind colors used: `red-500`, `green-500`, `red-400`, `yellow-400` (status, log levels).

## Tailwind Config
```js
tailwind.config = {
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        surface: 'var(--color-surface)',
        card: 'var(--color-card)',
        border: 'var(--color-border)',
        accent: '#25D366',
        accent2: '#00a8ff',
        primary: 'var(--color-text-primary)',
        secondary: 'var(--color-text-secondary)',
        muted: 'var(--color-text-muted)',
        inverse: 'var(--color-text-inverse)',
      }
    }
  }
}
```

## Layout
- **Container**: `max-w-7xl mx-auto px-4 py-6`
- **Grid**: 12-column `grid-cols-12 gap-4`
- **Stats row**: `col-span-12 grid grid-cols-6 gap-3` — 6 stat cards in one row
- **Conversation**: `col-span-8` — main content area
- **Sidebar**: `col-span-4` — profile + style analysis stacked vertically (`space-y-4`)
- **Log**: `col-span-12` — full width at bottom

## Stats Cards (6 columns)
```
bg-card border border-border rounded-xl p-4
```
- Label: `text-xs text-gray-500 mb-1`
- Value: `text-2xl font-bold` (accent color for uptime)

## Conversation Panel
- Container: `col-span-8 bg-card border border-border rounded-xl overflow-hidden`
- Header: `px-4 py-3 border-b border-border flex items-center justify-between`
  - Title: `text-sm font-medium`
  - Count: `text-xs text-gray-500`
- Body: `h-[480px] overflow-y-auto p-4 space-y-2`

## Message Bubbles
```css
.msg-wrapper { display: flex; margin-bottom: 4px; }
.msg-wrapper.out { justify-content: flex-end; }
.msg-wrapper.in { justify-content: flex-start; }
.msg-bubble { max-width: 75%; padding: 8px 14px; border-radius: 12px; font-size: 14px; line-height: 1.4; }
.msg-bubble.in { background: #1a2e1a; border-bottom-left-radius: 4px; }
.msg-bubble.out { background: #1a2744; border-bottom-right-radius: 4px; }
.msg-label { font-size: 11px; opacity: 0.6; margin-bottom: 2px; }
```
- **Incoming** (Pako): `msg-wrapper.in` → `msg-bubble.in` — green tint, left-aligned, label in `text-accent`
- **Outgoing** (conan bbg): `msg-wrapper.out` → `msg-bubble.out` — blue tint, right-aligned, label in `text-accent2`
- **Consecutive same-direction**: `border-radius: 12px; margin-top: -2px;` (removes extra radius on inner edges)

## Profile & Style Sidebar
- Section container: `col-span-4`
  - `bg-card border border-border rounded-xl p-4`
  - Title: `text-sm font-medium mb-3`
  - Content: `space-y-2 text-sm`
- Key-value rows: `<div class="flex justify-between"><span class="text-gray-500">Key</span><span class="text-white">Value</span></div>`
- Truncated values: `text-xs truncate max-w-[140px]` / `max-w-[120px]`

## Log Panel
- Container: `col-span-12 bg-card border border-border rounded-xl`
- Header: `px-4 py-3 border-b border-border flex items-center justify-between`
- Body: `h-48 overflow-y-auto p-4 font-mono text-xs space-y-0.5`
- Log entry: animation `fadeIn 0.2s ease-out`
- Colors by level: `ERROR` → `text-red-400`, `WARNING` → `text-yellow-400`, default → `text-gray-400`
- Timestamp: `text-gray-600`
- Max 200 entries visible, auto-scroll to bottom

## Header
- `flex items-center justify-between mb-8`
- Icon: `w-10 h-10 rounded-xl bg-accent/20 flex items-center justify-center` with SVG
- Title: `text-lg font-semibold`
- Status text: `text-xs text-gray-500`
- Status dot: `w-2.5 h-2.5 rounded-full` (green = running, red = stopped + `pulse`)
- Buttons group: `flex items-center gap-3`
  - Start: `px-4 py-2 rounded-lg bg-accent text-black text-sm font-medium hover:bg-accent/90 transition`
  - Stop: `px-4 py-2 rounded-lg bg-red-500/10 text-red-400 text-sm font-medium hover:bg-red-500/20 transition`
  - Theme toggle: `p-2 rounded-lg bg-card border border-border text-secondary hover:text-primary transition` — shows moon SVG in dark, sun SVG in light
  - Refresh: `p-2 rounded-lg bg-card border border-border text-secondary hover:text-primary transition`

## Animations
```css
@keyframes fadeIn { from { opacity: 0; transform: translateY(-4px); } to { opacity: 1; transform: translateY(0); } }
@keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.5; } }
.log-entry { animation: fadeIn 0.2s ease-out; }
.pulse { animation: pulse 2s infinite; }
```

## Scrollbar
```css
* { scrollbar-width: thin; scrollbar-color: #2a2d35 transparent; }
```

## Body
`bg-surface text-gray-100 font-sans antialiased min-h-screen`

## Font
- `font-sans` (system sans-serif)
- `font-mono` for log panel (system monospace)
- `antialiased` enabled

## Theme Toggle JS
```javascript
function toggleTheme() {
  const root = document.documentElement
  const isDark = root.classList.toggle('dark')
  localStorage.setItem('theme', isDark ? 'dark' : 'light')
  updateThemeIcon(isDark)
}
// On load: checks localStorage → prefers-color-scheme → defaults to dark
```

## Theme Transition
```css
body { transition: background-color 0.3s, color 0.3s; }
```