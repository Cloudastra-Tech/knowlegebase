---
name: Knowledge assistant
description: Cited answers from a company's own documents, behind its own sign-in.
colors:
  # dark is the default theme; light ("Paper") values follow in comments
  background: "oklch(0.155 0.004 165)" # light: oklch(1 0 0)
  foreground: "oklch(0.93 0.004 165)" # light: oklch(0.145 0 0)
  card: "oklch(0.19 0.005 165)" # light: oklch(1 0 0)
  popover: "oklch(0.205 0.005 165)" # light: oklch(1 0 0)
  primary: "oklch(0.93 0.004 165)" # light: oklch(0.205 0 0)
  primary-foreground: "oklch(0.17 0.005 165)" # light: oklch(0.985 0 0)
  muted: "oklch(0.245 0.005 165)" # light: oklch(0.97 0 0)
  muted-foreground: "oklch(0.72 0.008 165)" # light: oklch(0.556 0 0)
  border: "oklch(1 0 0 / 9%)" # light: oklch(0.922 0 0)
  ring: "{colors.signal}" # light: oklch(0.708 0 0)
  signal: "oklch(0.82 0.15 160)" # light: oklch(0.52 0.12 160)
  success: "oklch(0.8 0.14 158)" # light: oklch(0.52 0.12 158)
  warning: "oklch(0.84 0.14 80)" # light: oklch(0.56 0.12 70)
  destructive: "oklch(0.704 0.191 22.216)" # light: oklch(0.577 0.245 27.325)
typography:
  display:
    fontFamily: "Geist, ui-sans-serif, system-ui, sans-serif"
    fontSize: "3.75rem"
    fontWeight: 600
    lineHeight: 1.05
    letterSpacing: "-0.025em"
  title:
    fontFamily: "Geist, ui-sans-serif, system-ui, sans-serif"
    fontSize: "1.5rem"
    fontWeight: 600
    lineHeight: 1.33
    letterSpacing: "-0.025em"
  body:
    fontFamily: "Geist, ui-sans-serif, system-ui, sans-serif"
    fontSize: "0.875rem"
    fontWeight: 400
    lineHeight: 1.43
  reading:
    fontFamily: "Geist, ui-sans-serif, system-ui, sans-serif"
    fontSize: "0.9375rem"
    fontWeight: 400
    lineHeight: 1.7
  eyebrow:
    fontFamily: "Geist Mono, ui-monospace, monospace"
    fontSize: "0.75rem"
    fontWeight: 400
    lineHeight: 1.33
  ascii:
    fontFamily: "Geist Mono, ui-monospace, monospace"
    fontSize: "0.6875rem"
    fontWeight: 400
    lineHeight: 1.15
rounded:
  md: "8px"
  lg: "10px"
  xl: "14px"
  2xl: "18px"
  3xl: "22px"
  full: "9999px"
spacing:
  unit: "4px"
  gutter: "16px"
  panel: "20px"
  section: "80px"
  section-lg: "112px"
glass:
  # dark fill; light ("Paper") in comments
  pane: { blur: "8px", saturate: "160%", fill: "card at 30%" } # light: oklch(0.975) at 62%
  chrome: { blur: "16px", saturate: "160%", fill: "card at 58%" } # light: white at 64%
  floating: { blur: "12px", saturate: "160%", backdrop: "brightness(0.85)", fill: "popover at 60%" } # light: white at 62%, brightness(1.12)
  subtle: { blur: "none", fill: "card at 72%" }
motion:
  ease-out-strong: "cubic-bezier(0.23, 1, 0.32, 1)"
  ease-in-out-strong: "cubic-bezier(0.77, 0, 0.175, 1)"
  ease-drawer: "cubic-bezier(0.32, 0.72, 0, 1)"
  ease-hover: "cubic-bezier(0.25, 0.1, 0.25, 1)"
---

# Design System: Knowledge assistant

## Overview

**Creative North Star: "Signal Glass"**

A dark, quiet workbench. A slow ASCII field moves behind frosted glass chrome. One mint signal
colour marks what is live, cited or focused. Answers are set on a calm, nearly opaque reading
column where nothing moves. The ASCII appears where the machine is working (thinking, indexing,
loading, empty) and stays out of where the person is reading.

The system is shadcn/ui (`radix-nova`, CSS variables) on Tailwind v4, with the tokens in
`frontend/src/index.css`. Every shadcn variable keeps its name; Signal Glass adds `signal`,
`success`, `warning`, the `glass-*` set, the `ascii-*` inks and the motion tokens.

Two registers share the tokens. The marketing register (landing, sign-in and the other auth
pages) is always dark, with the ASCII brain, larger type, pill buttons and a serif italic accent.
The working register (chat, knowledge, admin) uses 32px controls, 14px body text and one 24px
title per screen.

**Status.** Every screen is on Signal Glass: the tokens, the theme, print, the motion tokens,
the shared ASCII loop, the primitives, the ASCII and glass kit, the shell, chat, the marketing
and auth pages and every app screen. The values below are the ones in the code after tuning.
The operator console and the browser extension get the tokens only.

## Theme

- **Dark is the default; Light ("Paper") and System are choices.** `next-themes`
  (`main.tsx`) puts the `dark` or `light` class on `<html>`, so dialogs, menus and toasts, which
  render into `<body>`, follow the theme. The choice is kept in `localStorage` (`theme`);
  `public/theme-init.js` applies it before the first paint (the CSP allows no inline script).
- **The marketing register is always dark**, whatever the choice.
- **Reading on dark:** foreground is `oklch(0.93 …)`, never pure white; the ground is
  `oklch(0.155 …)`, never black. Answers are 15px at line-height 1.7 and at most 68ch wide.
- **Print is always light:** black on white. `@media print` resets the tokens and the `dark:`
  variant never applies in print (`@custom-variant dark` sits inside `@media not print`).
  Screen chrome carries `data-print="hide"`; scrolling boxes carry `data-print="flow"`.
- The browser extension's side panel imports the same `index.css` and stays light. Its
  `panel.css` lists the frontend files whose classes it needs (`@source`): `components/ui`
  and the ASCII spinner that Button's `loading` draws.

## Colors

One hue, **Signal mint** (`--signal`). It means *live, cited, focused or done*. It is the focus
ring, the active marker, the citation and the ASCII accent. It is never a large fill, a primary
button or body text. Primary stays achromatic: near-white ink on dark, graphite on light. The
dark neutrals carry a faint 165° tint (chroma 0.004–0.006) that ties them to the mint without
reading as colour.

| Token | Use |
|---|---|
| `background`, `card`, `popover` | the ground, raised surfaces, floating surfaces (dark: 0.155 / 0.19 / 0.205) |
| `foreground`, `muted-foreground` | text; secondary text (descriptions, labels, inactive nav) |
| `primary` / `primary-foreground` | the one strong action per area; near-white on dark |
| `muted`, `accent`, `secondary` | rails, hovers, chips |
| `border`, `input` | hairlines (9% and 14% white on dark) |
| `ring` | focus; the signal on dark |
| `signal` / `signal-foreground` | live, cited, focused, done |
| `success`, `warning`, `destructive` | status; always with a glyph as well as the colour |
| `chart-1` … `chart-5` | charts: signal first, then teal, grey-green, amber, grey |
| `sidebar-*` | the app sidebar; `sidebar-primary` is the signal |
| `ascii-ambient`, `ascii-field`, `ascii-ink`, `ascii-signal` | the app's background field (9% on dark, 5% on Paper), the landing field (16%), the brain (85%), its sparks |

### Named rules
**The One Signal Rule.** Emphasis comes from weight, size and the step between foreground and
muted text. The signal marks state, never hierarchy. There is no second accent.

**The Status Token Rule.** Success is `success`, warning is `warning`, errors are
`destructive`. Never raw Tailwind palette colours (`emerald-*`, `amber-*`, `blue-*`); there
are none left in `frontend/src`.

**Contrast on the worst composite.** Check text contrast on glass at its lowest alpha over a field
glyph at its highest alpha, not on the flat token.

## Glass

Four tiers, as utilities in `index.css`. Never glass inside glass. The look is real blur over
real content with one hairline: no glow, no coloured edge, no gradient.

| Tier | Utility | Blur | Fill: dark / Paper | For |
|---|---|---|---|---|
| Pane | `glass-pane` | 8px, saturate 160% | card at 30% / oklch(0.975) at 62% | the app sidebar, open and as the rail; one `--glass-edge` hairline on its inner edge |
| Chrome | `glass` | 16px, saturate 160% | card at 58% / white at 64% | the sticky screen strip (`ScreenStrip`), the phone top bar, the composer and the new chat's Recent chats card, the landing header and cards |
| Floating | `glass-strong` | 12px, saturate 160%, what's behind dimmed to 0.85 (lifted to 1.12 on Paper) | popover at 60% / white at 62% | popovers, menus, selects, hover cards, tooltips, ⌘K, dialogs, sheets (the phone's sidebar too), toasts (no dim or lift: Sonner's own classes) |
| Subtle | `glass-subtle` | none | card at 72% | panels and cards inside a screen |

`glass-noise` adds static grain to a large glass surface; it never animates. The dialog, sheet
and palette overlays are a plain 40% dim, so the floating glass above them frosts the screen.

- **Something to frost.** The app's field (and the chat's) runs full-bleed, under the sidebar.
  Its glyphs are dots and dashes, which a blur averages to nothing at the ambient ink, so it's
  drawn at full ink and masked back to the ambient ink beside the sidebar (`ascii.css`): only
  the pane sees the strong field, as a slow soft frost. The empty new chat's drift does
  the same in its own way: it runs under the sidebar too, and its lines are drawn at full ink
  under the pane, the composer and the Recent chats card, so all three frost a strong line
  (`redacted-drift.tsx`, `data-drift`).
- **Contrast** (on the worst realistic composite: blurred body text at 25% of its ink, or the
  field at full ink): muted text is 6.0:1 or more on dark glass, foreground 12:1. On Paper the
  muted text on glass is a step darker (`oklch(0.5)`; plain 0.556 is only 4.7:1 on white, and
  any frost takes it under 4.5:1), which keeps it at 4.9:1 or more. A large solid patch
  (a primary button) right under a menu's muted text can still take it under 4.5:1; "Reduce
  effects" makes every tier solid for anyone who needs that.
- **Fallbacks:** without `backdrop-filter`, under `prefers-reduced-transparency: reduce`, or with
  the app's "Reduce effects" (`:root[data-effects="reduced"]`, set by `setEffects()` in
  `lib/ascii-loop.ts`), every tier goes solid and `backdrop-filter` is `none` (not a 0px blur,
  which is still a pass). Touch screens get 12px chrome. Print is solid.
- **Budget, measured** (headless Chrome on the real GPU, an Intel UHD 620, 1280×800, dark; 5s of
  wheel scrolling, 10s idle, 10s idle with the account menu open; the median of 3 runs). The
  first pass had a 12px pane, rejected for the activity scroll:

  | Screen, phase | Before (no blur on the sidebar) | Now | First pass, 12px pane |
  |---|---|---|---|
  | /chat, scrolling | 58 fps, p95 16.8ms, GPU 436ms/s | 59 fps, 16.7ms, 425 | 59 fps, 16.8ms, 533 |
  | /chat, idle | 60 fps, 16.8ms, GPU 106 | 60 fps, 16.7ms, 49 | 60 fps, 16.7ms, 84 |
  | /knowledge, idle | 60 fps, 16.8ms, GPU 153 | 60 fps, 16.7ms, 50 | 59 fps, 16.8ms, 62 |
  | /admin/activity, scrolling (under the strip) | 60 fps, 16.7ms, GPU 813 | 60 fps, 16.8ms, 619 | **48 fps, 33.4ms**, 976 |
  | any, a menu open 10s | 60 fps, GPU < 1 | 60 fps, GPU < 1 | 60 fps, GPU < 1 |
  | "Reduce effects", idle | | 60 fps, GPU 0 to 0.3 | |

  GPU busy time moves by half between runs on a laptop; the frame rate and p95 are the
  steady signal.

  So the cap is no longer a count of blurred surfaces: it's **one pane (the sidebar) at 8px,
  plus the chrome pieces (16px) and one floating stack (12px)**. A pane blur over 8px over the
  moving field, or a second large pane, is what to measure before adding. No full-viewport blur.
  The app field runs at 12 fps and rests while scrolling and while a menu or dialog holds the
  page, which is what keeps the glass above it cheap: with a menu open nothing re-blurs.
- **The backdrop-root trap.** An ancestor with `opacity < 1`, `filter`, `mask`, `clip-path` or
  `will-change` on those becomes the backdrop root, and the blur inside it stops seeing the page.
  Never animate `opacity` on a wrapper that contains glass; animate the content inside it.
- No `will-change: backdrop-filter` unless a trace shows the need.
- Tables, form fields, dialog bodies and charts are never glass.

## Typography

**Sans:** Geist. **Mono:** Geist Mono. Both load from Google Fonts (`index.html`); the extension
uses the system fonts. The light serif italic (the system serif) is marketing-only: one phrase in
a landing, sign-in or auth heading.

| Role | Spec |
|---|---|
| Screen title | Sans 600, 24px, tracking −0.025em, `text-balance`; one per screen, an `h2` (the e2e specs wait for it) |
| Breadcrumbs | Sans 14px, muted, the page itself in the foreground; lucide `ChevronRight` separators (see "Breadcrumbs") |
| Section label | Mono 12px with a rule: `── providers ─────────` |
| Body / UI | Sans 400, 14px / 1.43 |
| Reading (answers, passages, shared chats) | Sans 400, 15px / 1.7 (`text-reading`), at most 68ch, `text-pretty`. Never mono, never over moving ASCII |
| Figures | `tabular-nums` for counts, timestamps, IDs, key prefixes |
| ASCII art | Mono 11px / 1.15 (`text-ascii`), `select-none`, `aria-hidden` |
| Kbd | Mono 12px |

**The Tight Heading Rule.** Semibold headings use tight tracking and `text-balance`; body text
never does. **No off-scale sizes:** `text-reading` and `text-ascii` are the only additions to
Tailwind's scale. `cn()` (`lib/utils.ts`) knows them as font sizes, so they survive next to a
text colour; import `cn` from there, never from the `cn` package itself (the shadcn CLI writes
`from "cn"` into new primitives: change it).

## ASCII

| Place | What | Motion |
|---|---|---|
| App background | one fixed field, full-bleed: under the sidebar at full ink (`--ascii-backdrop`) for its glass to frost, and beside it at `--ascii-ambient` (the mask's `--ascii-beside`), at that strength out to 36rem from the content's centre (the widest screen's edge, so the sides read as ASCII at 1280), fading to 15% by 26rem in, under the content; 15% everywhere on a phone. The chat draws its own the same way (`chat-backdrop.tsx`, 22% under the reading column, a mint glow while thinking) | 12 fps; rests while scrolling, under a dialog, offscreen, in a hidden tab; still with reduced motion or "Reduce effects" |
| Thinking | a braille spinner plus a status line that decodes on change | 80ms frames; decode ≤ 240ms |
| Inline loading | an ASCII spinner in place of `Loader2` | 80ms `steps()`; a static `•••` with reduced motion |
| Empty states | 6–14 rows of hand-drawn art with real text beside it | one scan or blink, no faster than every 2s |
| The empty new chat | "Redacted drift" (`redacted-drift.tsx`), in the chat field's place, fixed to the window (under the sidebar too, as the chat's field is), top to bottom: lines of made-up records 42–60px apart (7.5% of the height), one through the composer's middle, at three depths (8–12% of the text colour), redacted (`▪▪▪ · ░░`, word lengths kept) except in a zone round the composer, where the blocks are lighter and about every 8s one line decodes (faint mint on dark, grey on Paper), going round the lines. What sits over the art says so with `data-drift`: under words (the heading, the starters, the key hint, the fine print: `content`, `quiet`) the lines are drawn at 30%, so the words always read; under glass (`composer`, `glass`: the composer, the Recent chats card) and under the sidebar's pane at full ink (64–96% of the text colour), since a blur averages faint dashes to a flat tone: the glass, not a faded line, keeps the words on it readable. Nothing reads under either: no decode, no lens. With a mouse, a soft reading lens (about 100px round the pointer, 84px under 640px) resolves the cells inside into the line's own record through a 170ms scramble, at most 42% of the text colour (48% on Paper) with a soft rim, and closes them again (150ms) as it moves on. A click on blank space on or near a line (within 30px; a tap takes the nearest) decodes the whole line outwards from it, readable for 8s, with one faint ring that only fades (380ms). The page says one heading and the composer; who you search as and the logging notice are the one line at its foot | 30 fps on the ambient budget, 10–20 px/s; focus or typing slows it to 30% and draws near lines 4–8px in; the lens follows the pointer with a 60ms lerp, fine pointers that hover only; the pointer is read on the chat column (the canvas takes no events) into the scene, never React state, and a press on blank space keeps the focus where it is; fades out in 400ms when the first question is sent; a still frame with one line readable with reduced motion or "Reduce effects", where the lens and clicks do nothing. Only ever the made-up records. Never on a saved chat or a shared one |
| Section headers, timelines, progress | box-drawing rules, `├─` rows, `[██████░░░░] 62%` | static |
| Streaming cursor | `▌` in signal | 1s blink; steady with reduced motion |
| Landing and sign-in | the brain, the field, the ticker | 30 fps |

**Never** under or inside: answer and passage text, markdown tables, the draft editor, dense
tables, form fields, dialog bodies, charts, chat-history rows, toast text, print. All art is
`aria-hidden` with real text beside it.

## Motion

Tokens in `index.css` (and `lib/motion.ts` for Motion):

- **Curves:** `ease-out-strong` (enter, exit, feedback), `ease-in-out-strong` (movement on
  screen), `ease-drawer` (sheets, the sidebar), `ease-hover` (colour and background).
- **Durations:** press 120ms, hover 150ms, tooltip 140ms, popover 180/120ms, dialog 220/150ms,
  sheet 280/200ms, screen 200ms, swap 150ms, reveal 240ms; used as `duration-(--dur-popover)`.
  The chat adds Jump to latest 150/100ms, a step line 180ms, the steps folding 250ms and a
  citation chip resolving 160ms; an answer's sources stagger 30ms (`--stagger-sources`).
- **Distances:** `--enter-y` 6px, `--enter-scale` and `--press-scale` 0.97. All zero under
  reduced motion.
- **Stagger:** 40ms apart, at most 6 items, first mount only: `useStaggerOnce(ready)` in
  `lib/motion.ts` gives each item its class and delay for the first draw, then nothing, so a
  refetch, a filter or a new item doesn't animate (the Integrations and My connections grids).
  Never on tables or lists read closely.

| Interaction | Rule |
|---|---|
| Anything from the keyboard (⌘K, ⌘B, shortcuts, tab arrows) | **Never animates.** Call `markInstant()` first; `:root[data-instant]` turns off transitions and animations on `[data-screen]` and `[data-slot]` for two frames |
| Press | `scale(var(--press-scale))`, 120ms `ease-out-strong`; not on menu triggers |
| Hover | colour and background only, 150ms; custom CSS hovers sit inside `@media (hover: hover) and (pointer: fine)` |
| Tooltip | 140ms fade and scale from its origin; none when Radix opens it instantly |
| Popover, menu, select | 180ms in / 120ms out, origin-aware |
| Dialog | 220ms in / 150ms out, from the centre |
| Sheet | full-edge slide, 280 / 200ms, `ease-drawer` |
| Screen change | content rises `--enter-y` with a fade on non-glass children, 200ms; no exit |
| New chat → first question | the empty chat is centred: the heading and the composer in the middle of the column, the recent chats under them. Sending moves the composer down to its place in one transform (FLIP, `reveal` 240ms, `ease-out-strong`), at once with reduced motion or "Reduce effects"; the heading and the sections go at once. Opening a saved chat or New chat changes the layout without moving anything |
| Chat | a question just sent is pinned to the top and its answer grows below it; nothing scrolls while it streams, and "Jump to latest" is instant with reduced motion. New messages rise 6px; history never animates (a chat opened from history shows Jump to latest without its entrance until it's scrolled or asked in); citations stagger once on completion |
| Toast | Sonner's own motion |

**Reduced motion is "fewer and gentler":** movement goes, fades that help understanding stay.
tw-animate's translate and scale are zeroed, smooth scrolling is off, sheets fade, staggers
collapse, and every ASCII piece draws one still frame. `MotionConfig reducedMotion="user"` wraps
the app.

**Which tool:** CSS (transitions, tw-animate `data-state` classes) for every Radix primitive,
hover, press, screen entrance, spinner and shimmer. Motion (`motion/react`) for React-state
animation that must be interruptible or needs presence, with `transform` strings while text
streams and no `layoutId` in the shell. `lib/ascii-loop.ts` for every ASCII frame, never CSS or
Motion.

Never: `transition-all`, `ease-in` on its own, `scale-0` or `zoom-in-0`, `animate-pulse` for
loading, an animation that blocks a click.

### The ASCII loop (`lib/ascii-loop.ts`)

- **One `requestAnimationFrame`** for every subscriber (`subscribe(el, draw, { fps, still,
  priority })`, `useAsciiLoop`, and the original `runLoop`). It is idle when nothing is visible.
- **Caps:** landing hero 30 fps, sign-in panel 24, ticker 30, the app field 12, spinners 12.5,
  the empty chat's drift 30 (ambient: it rests and steps down like the field).
- **Rests** offscreen (IntersectionObserver), in a hidden tab, and, for the app field
  (`priority: "ambient"`), while the page scrolls (150ms) and while a dialog holds the page
  (Radix's `data-scroll-locked` on `<body>`); `pauseFor(ms)` rests it on demand.
- **Still frame** with reduced motion or `setEffects("reduced")`, following a change live.
- **Budget:** the app's pieces (the field, spinners, the thinking line) get 4ms of drawing per
  frame between them. When the 95th percentile of 30 drawn frames is over, the field steps down
  to 6 fps, then still; under 2ms it steps back up, and a still field tries moving again after
  10s (doubling, up to 160s, each time it has to stop again). The landing's hero gets 8ms: it is
  alone on its page, and a lower frame rate can't make a draw cheaper. Past 8ms it runs at 20
  fps until draws are under 6ms. A piece's first 10 frames don't count. In development it warns
  once when frames overrun three in a row.
- **Fonts:** measure the cell with `whenFontsReady(el)`; measured earlier it is the fallback
  font's. Re-measure on resize.

## Layout

- **Base unit:** 4px. Page gutter 16px at every width. No sideways scrolling at 390px.
- **Containers:** 1152px (`max-w-6xl`) for the landing; 1024px (`max-w-5xl`) for
  settings screens; 768px (`max-w-3xl`) for the chat column; 68ch for reading text.
- **Shell:** no top bar in the app (see "App sidebar"); a 48px top bar on a phone; the
  landing's header is sticky, 56px.
- **Rhythm:** screens pad 24px top and bottom; panels pad 20px; fields stack at 20px. Landing
  sections breathe at 80px, 112px from `sm`.

## Shapes

`--radius` is 10px; the scale multiplies it: 8px (tab triggers in a rail), 10px (anything you
click or type into), 14px (cards, the sign-in card), 18px (app panels, chat bubbles), 22px (the
composer, the landing CTA band), full pill (badges, avatars, status dots, chips, landing buttons).
Borders are 1px hairlines.

## Components

- **Controls** are the shadcn primitives in `frontend/src/components/ui` (`Button`, `Input`,
  `Textarea`, `InputGroup`, `Select`, `Field`, `Tabs`, `Badge`) and the app's own pickers
  (`components/date-picker`, `components/month-range-picker`). Never a raw `<input>`,
  `<select>`, `<textarea>`, or a native date or month input. A missing primitive comes from the
  shadcn CLI.
- **Buttons:** 32px (28 `sm`, 36 `lg`), 10px radius. Primary is near-white ink on dark. Focus is
  the signal ring. Variants: `default`, `outline`, `secondary`, `ghost`, `destructive`, `link`,
  and `glass` (a secondary action sitting on glass: the subtle fill and a hairline highlight).
  `loading` makes it busy and disabled with the ASCII spinner before the label; the label says
  what's happening ("Saving…"). Transitions name their properties (colour, background, border,
  shadow, transform), 120ms; press scales to `--press-scale`.
- **Badges:** pill, 20px. `mono` (states and roles: live, soon, admin; mono on a hairline),
  `signal` (live, cited, done: signal text on a faint signal tint) and `warning` (a flag on a
  row, such as Knowledge's mono `!` on an item the scan found instructions to an AI in: warning
  text on a warning hairline, with the words in an `sr-only` span and the tooltip), besides
  shadcn's own.
- **Cards:** `variant` `default` (opaque card, glass hairline), `glass` (the chrome tier) or
  `subtle` (`glass-subtle`).
- **Tabs:** `default`, `line`, and `glass` (a translucent rail with one sliding thumb, instant
  from the keyboard). `panels={false}` for tabs used as a switch (a filter, a URL per tab).
- **Panels:** 18px radius, hairline, 20px padding, `glass-subtle` in the redesigned screens.
  `description` is the muted line under the title, across the panel's width; it also describes
  the region. Don't fake it with a first child and a negative margin.
- **Tables:** opaque (`bg-card`), solid sticky header, `tabular-nums`, no stagger, no glass;
  `TableCell mono` for IDs and timestamps.
- **Toasts:** Sonner on the strong glass tier, the status as a mono glyph in its token colour
  (`✓ i ! ×`), the ASCII spinner for loading. No `richColors`.
- **Switch, Toggle:** colour, background, border and shadow transitions only, 150ms.
- **Route pass** (sign-in): as a work email is typed, a strip under the field resolves the
  workspace, the sign-in method and where roles come from; `aria-live="polite"`, debounced 350ms;
  values rise in 300ms, motion-safe only.
- **App sidebar** (`app-sidebar.tsx`, on the shadcn `Sidebar` with `collapsible="icon"`): one
  left sidebar on every signed-in screen, no top nav. 260px open: the wordmark and the
  collapse button, New chat and Search chats (the palette with chats alone), the space, the
  screens, the Admin group, the chats (scrolling on their own), the index status and the
  account (avatar, name, workspace, role badge; the account menu holds View as, the personal
  screens, chat retention, the theme, Reduce effects and signing out). Collapsed, a 56px rail
  of icons with tooltips that never goes away (kept in `localStorage` `kb.sidebar`; ⌘B at
  once, the button slides the panel 200ms `ease-drawer` while the reserved width snaps). On a
  phone, a sheet from the left behind a 48px top bar (menu, the screen's name, New chat).
  The sidebar is the pane glass (`glass-pane`) over the field, open and as the rail, with one
  hairline (`--glass-edge`) on its inner edge; the phone's sheet is the floating tier.
  Hover is a soft background; the open screen a soft background and its icon in the signal.
  Never an edge bar or stripe. The index progress is a 2px signal line along the top of the
  content; the strips (trial, support, deletion) sit above the screen.
- **New chat home** (`chat-home.tsx`, under the composer on the empty new chat, as on
  Claude.ai): one column, the composer's width and left edge, on an 8px rhythm. Someone with
  chats sees Recent chats, the last five (four on a phone), in one glass card (the chrome tier, as the composer): a
  small muted label with View all (Search chats) as a quiet ghost button in its row, then
  rows of a Lucide chat icon, the title and how long ago (muted, tabular, on the right), a
  soft background on hover. Someone without chats sees the starter questions instead, as
  quiet left-aligned chips (and, on a first run, where they come from and the sample
  handbook), drawn only once the chats are in so they never show and then go. No Spaces or
  Folders: the composer's space chip and the sidebar have them. The keys ("Enter to send…",
  "Esc to stop") are a small line under the composer, on its inner left edge, only while it's
  focused and empty or an answer is being written; the row keeps its height. Who you search
  as and the logging notice are one muted line at the foot of the page, on the same left
  edge, clear of a phone's home bar. The space under the composer is shared 1 : 1.45 with
  the space above the heading, so the composer sits a little above the middle; at 1366 × 728
  everything fits without scrolling, and the recent chats scroll on their own if they
  don't.
- **Kit:** `/__kit` (development only) shows every piece in either theme over the app's field;
  `/__kit?view=backdrop` is the field alone behind a plain screen, for a performance trace.
- **Screen shell:** `ScreenHeader` (breadcrumbs, the `h2`, description, actions) and `Panel`
  (`subtle` by default; `glass` straight on the field; `solid` for reading and tables).
  `GlassPanel` is a bare surface of one tier. `ScreenStrip` (in `Screen`, from 768px): once
  the `h2` has scrolled under the top, a 44px chrome-glass strip with the title fades in
  (150ms) over the content scrolling beneath it, lined up with the title; `aria-hidden`,
  since the `h2` is the title.
- **Breadcrumbs** (`components/breadcrumbs.tsx` on shadcn's `Breadcrumb`; the model is
  `lib/breadcrumbs.ts`, one for every screen, from the URL plus what the screen passes:
  a space's name, its open tab, a draft's title). Over every screen's title: the sidebar's
  screen first (`Spaces › Marketing team › Sources`, `Access › Webhooks`, `Insights › Not
  found`, `Documents › Drafts › Q3 handbook`), the personal screens under `Account`, a menu
  of the account menu's screens. `<nav aria-label="Breadcrumb">` with an `<ol>`; every
  ancestor a router link (instant, `markInstantNav`; the chat stays as it was), the page
  plain text with `aria-current="page"`. Hover underlines; no background, no edge. Middle
  crumbs truncate at 12rem; past three crumbs the middle fold into a `…` menu. A tab with
  its own URL (Access, Insights, a space's `?tab=`) is the last crumb, and the tab bar
  stays. On a phone a detail view (a space, a draft, a shared chat) shows `← Spaces`, its
  logical parent (never history), in their place. The chat's header is `All company ›
  title` or the space's name, the space from `sm` up.
- **ASCII pieces** (`components/ascii/*`): `AsciiField`, `AppBackdrop`, `AsciiSpinner` (for
  `Loader2`), `AsciiThinking`, `ScrambleText`, `AsciiEmptyState`, `AsciiDivider`,
  `AsciiProgress`, `AsciiLoader`, `RedactedDrift` (the empty chat; the composer's focus and
  typing reach it through `composer-signal.ts`, not React state). New fields draw on canvas: axe weighs `aria-hidden` text on
  contrast too, and a dim `<pre>` with nothing over it fails. Art in empty states uses the full
  `muted-foreground` for the same reason.
- **Glass pieces** (`components/glass/*`): `Panel`, `GlassPanel`, `StatusDot` (live, syncing,
  warning, error, off; a ping ring only for live, motion-safe; the state in words beside it).
- **Motion helpers** (`lib/motion.ts`): `ease`, `dur`, `spring` and `stagger` mirror the CSS
  tokens for Motion; `useStaggerOnce`; `markInstant()` before a keyboard-driven navigation;
  `usePrefersReducedMotion()` for what JS decides (a scroll's `behavior`).
- **Reduce effects** is kept in `localStorage` (`effects`) and applied before the first paint
  by `theme-init.js`.

## Checks

- `frontend/e2e/motion.spec.ts`: the app field's canvas doesn't change over 1s with reduced
  motion (and moves again when it's switched off); `html.dark` is the default and a Light choice
  survives a reload; the sign-in page and a toast on it are dark with Light chosen; a chat
  opened from history has no running animation.
- `frontend/e2e/empty-chat-art.spec.ts`: an empty /chat has the drift as its one field, moving,
  and still with reduced motion; once a question is sent it's gone and the chat's field moves;
  a saved chat opened from an empty one has no drift and no running animation.
- `frontend/e2e/home-center.spec.ts`: the empty chat's composer is near the middle at 1280
  and 390, with the fine print at the foot of the page on its left edge; the key hint only
  while it's focused and empty, its row keeping its height; the art's canvas is the whole
  column with lines from top to bottom, and the composer has a backdrop blur (`none` with
  "Reduce effects"); recent chats are in one glass card on the composer's width and edge,
  with no Spaces, Folders or starters, and one opens; sending moves the composer down in one
  move of 250ms at most (none with reduced motion) and the questions pin to the top; a mouse
  over the art reads the lines near it, above the heading and beside the recent chats, a
  click decodes a line and keeps the composer's focus; with reduced motion neither changes a
  pixel.
- `frontend/e2e/ux-a11y.spec.ts`: axe on every main screen at 1280 and 390; at 390 the page fits
  and no scrolling box inside it is pushed sideways (`overflowingScrollers`; a box meant to scroll
  sideways carries an `overflow-x-auto` or `overflow-auto` class).
- `frontend/e2e/owner-shots.spec.ts`: with `E2E_OWNER_SHOTS=<folder>`, every screen in both
  themes at 1280 and 390, from the e2e server's fake data, as `<screen>-<theme>-<width>.png`.
- Grep guards over `frontend/src`: no raw `<input`, `<select`, `<textarea`; no raw palette
  colours; no `transition-all`; no `ease-in` on its own; no `scale-0` or `zoom-in-0`; no
  `Loader2`, `animate-pulse` or `animate-spin` outside the kit.

## Do's and Don'ts

### Do
- **Do** use the tokens: `signal`, `success`, `warning`, `destructive`, `muted-foreground`.
- **Do** give every status a glyph as well as a colour.
- **Do** keep one `h2` title per app screen.
- **Do** keep reading text still, opaque behind, 15px / 1.7, at most 68ch.
- **Do** draw ASCII through `lib/ascii-loop.ts`, `aria-hidden`, with a still frame.
- **Do** check both themes, both widths (1280 and 390) and reduced motion.

### Don't
- **Don't** add a second accent, or use the signal for fills, primary buttons or body text.
- **Don't** add raw Tailwind palette colours (`emerald-*`, `amber-*`, ...).
- **Don't** nest glass, blur the full viewport, or animate opacity on a wrapper around glass.
- **Don't** animate anything the keyboard triggers, or use `transition-all`.
- **Don't** put moving ASCII under text, in tables, forms, dialogs, charts or print.
- **Don't** use native `<input>`, `<select>`, `<textarea>` or date/month inputs.
- **Don't** add off-scale font sizes (`text-[11px]`, `text-[15px]`); use `text-ascii` or `text-reading`.
