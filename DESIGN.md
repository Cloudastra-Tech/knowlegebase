---
name: Knowledge assistant
description: Cited answers from a company's own documents, behind its own sign-in.
colors:
  primary: "oklch(0.205 0 0)"
  primary-foreground: "oklch(0.985 0 0)"
  background: "oklch(1 0 0)"
  foreground: "oklch(0.145 0 0)"
  muted: "oklch(0.97 0 0)"
  muted-foreground: "oklch(0.556 0 0)"
  border: "oklch(0.922 0 0)"
  ring: "oklch(0.708 0 0)"
  destructive: "oklch(0.577 0.245 27.325)"
typography:
  display:
    fontFamily: "Plus Jakarta Sans, ui-sans-serif, system-ui, sans-serif"
    fontSize: "3.75rem"
    fontWeight: 600
    lineHeight: 1.05
    letterSpacing: "-0.025em"
  headline:
    fontFamily: "Plus Jakarta Sans, ui-sans-serif, system-ui, sans-serif"
    fontSize: "2.25rem"
    fontWeight: 600
    lineHeight: 1.11
    letterSpacing: "-0.025em"
  title:
    fontFamily: "Plus Jakarta Sans, ui-sans-serif, system-ui, sans-serif"
    fontSize: "1.5rem"
    fontWeight: 600
    lineHeight: 1.33
    letterSpacing: "-0.025em"
  section:
    fontFamily: "Plus Jakarta Sans, ui-sans-serif, system-ui, sans-serif"
    fontSize: "1rem"
    fontWeight: 500
    lineHeight: 1.5
  body:
    fontFamily: "Plus Jakarta Sans, ui-sans-serif, system-ui, sans-serif"
    fontSize: "0.875rem"
    fontWeight: 400
    lineHeight: 1.43
  label:
    fontFamily: "Plus Jakarta Sans, ui-sans-serif, system-ui, sans-serif"
    fontSize: "0.75rem"
    fontWeight: 500
    lineHeight: 1.33
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
  card: "24px"
  section: "80px"
  section-lg: "112px"
components:
  button-primary:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.primary-foreground}"
    typography: "{typography.body}"
    rounded: "{rounded.lg}"
    padding: "0 10px"
    height: "32px"
  button-outline:
    backgroundColor: "{colors.background}"
    textColor: "{colors.foreground}"
    typography: "{typography.body}"
    rounded: "{rounded.lg}"
    padding: "0 10px"
    height: "32px"
  button-outline-hover:
    backgroundColor: "{colors.muted}"
  button-secondary:
    backgroundColor: "{colors.muted}"
    textColor: "{colors.primary}"
    rounded: "{rounded.lg}"
    height: "32px"
  button-marketing:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.primary-foreground}"
    rounded: "{rounded.full}"
    padding: "0 24px"
    height: "36px"
  input:
    backgroundColor: "{colors.background}"
    textColor: "{colors.foreground}"
    typography: "{typography.body}"
    rounded: "{rounded.lg}"
    padding: "4px 10px"
    height: "32px"
  tabs-list:
    backgroundColor: "{colors.muted}"
    textColor: "{colors.muted-foreground}"
    rounded: "{rounded.lg}"
    padding: "3px"
    height: "32px"
  tabs-trigger-active:
    backgroundColor: "{colors.background}"
    textColor: "{colors.foreground}"
    rounded: "{rounded.md}"
  panel:
    backgroundColor: "{colors.background}"
    rounded: "{rounded.2xl}"
    padding: "{spacing.panel}"
  auth-card:
    backgroundColor: "{colors.background}"
    rounded: "{rounded.xl}"
    padding: "{spacing.card}"
    width: "400px"
  route-pass:
    backgroundColor: "{colors.muted}"
    textColor: "{colors.foreground}"
    typography: "{typography.body}"
    rounded: "{rounded.lg}"
  route-pass-resolved:
    backgroundColor: "{colors.background}"
  badge:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.primary-foreground}"
    typography: "{typography.label}"
    rounded: "{rounded.full}"
    height: "20px"
---

# Design System: Knowledge assistant

## Overview

**Creative North Star: "The Plain Ledger"**

Knowledge assistant looks like a well-kept ledger: white paper, graphite ink, hairline rules, and nothing on the page that can't account for itself. The palette is achromatic. Hierarchy comes from weight, size and the step between graphite and grey text, never from hue. That restraint is what lets the few things that carry meaning (a cited source, a resolved sign-in route, a red error) read instantly.

The system is shadcn/ui on the `radix-nova` style with a neutral base colour, set entirely in Plus Jakarta Sans. Density is moderate: 32px controls, 14px body text, 20px panel padding. Surfaces are flat and outlined by default; at most one element per screen is lifted with a soft, layered shadow, and it is always the thing you act on (the sign-in card, the chat composer, the landing page's product preview).

Two registers share the same tokens. The marketing register (landing page) uses larger type, pill-shaped buttons and generous 80–112px section spacing. The working register (chat, Integrations, Sign-in settings, Members, the sign-in page) uses 10px-radius controls, 24px screen titles and 16–24px rhythm.

**Key Characteristics:**
- Achromatic OKLCH neutrals; graphite primary on white.
- One family, Plus Jakarta Sans, at weights 400 / 500 / 600; tight tracking on every heading.
- Hairline borders at reduced opacity (`border` at 70%) do the structural work; shadows are rare and soft.
- Rounded but not soft: 10px controls, 14px cards, 18px panels, pills reserved for marketing actions, badges and avatars.
- Things that resolve show their answer in place (cited sources under an answer, the route pass under the email field).

## Colors

A single graphite-and-white neutral ramp in OKLCH with zero chroma, plus one red for errors.

### Primary
- **Graphite Ink** (`{colors.primary}`): default buttons, the user's chat bubble, the landing CTA band, avatar fallback, active-nav underline. Its hover is the same ink at 80% opacity rather than a second colour.

### Neutral
- **Paper White** (`{colors.background}`): the page ground in the app and on the landing page; cards and panels sit on it at the same value and are separated by a border, not a fill.
- **Near-Black Text** (`{colors.foreground}`): all primary text and headings.
- **Fog** (`{colors.muted}`): tab rails, secondary buttons, outline-button hover, chips. At 30–50% opacity it is the sign-in page ground and the unresolved route pass.
- **Slate Grey Text** (`{colors.muted-foreground}`): descriptions, labels, inactive nav, the muted half of the wordmark ("assistant").
- **Hairline** (`{colors.border}`): every border and input stroke, usually drawn at 60–70% opacity (`border-border/70`, `border-border/60` on sticky headers).
- **Focus Grey** (`{colors.ring}`): focus rings, drawn at 50% as a 3px halo.
- **Paper Ink** (`{colors.primary-foreground}`): text on Graphite Ink.

### Error
- **Signal Red** (`{colors.destructive}`): field errors, invalid input strokes and halos (at 20%), destructive buttons (as a 10% tint with red text, never a solid red fill).

### Named Rules
**The No-Hue Rule.** Structure, hierarchy and emphasis are carried by the neutral ramp alone. Colour enters only as a semantic signal (error) or as a third party's own mark (provider logos, integration brand colour on hover).

**Status colours are not yet tokens.** Success and warning states currently use raw Tailwind emerald and amber utilities in eight components. That is recorded drift, not a rule; see Do's and Don'ts.

The `.dark` token set in `frontend/src/index.css` is defined and components carry `dark:` variants, but nothing in the app applies the `.dark` class, so the shipped product is light-only.

## Typography

**Display Font:** Plus Jakarta Sans (with ui-sans-serif, system-ui, sans-serif)
**Body Font:** Plus Jakarta Sans (same stack; `--font-heading` aliases `--font-sans`)
**Label/Mono Font:** the system monospace, only for machine strings (error reference codes, IDs), with tabular numerals.

**Character:** A single geometric-humanist sans used at three weights. Headings get their authority from semibold weight and tight tracking, not from a second typeface.

### Hierarchy
- **Display** (600, 2.25rem on phones to `{typography.display}` 3.75rem at `sm`, line-height 1.05, tracking tight): the landing hero headline only. Its second clause is set in Slate Grey Text.
- **Headline** (600, 1.875rem on phones to 2.25rem, up to 3rem for one landing section, tracking tight): landing section titles and the closing CTA band.
- **Title** (600, 1.5rem, tracking tight): one per screen. App screen titles (Integrations, Sign-in settings, the empty chat), and the sign-in card's heading.
- **Section** (500, 1rem): titles of bordered panels inside a screen (Members, a sign-in settings section).
- **Body** (400, 0.875rem, line-height 1.43): the workhorse; nearly every sentence in the app. Inputs render at 1rem on phones (to stop iOS zoom) and 0.875rem from `md`. The landing hero lede is the one 1.125rem paragraph.
- **Label** (500, 0.75rem): segment labels in the route pass, helper footnotes, the "Other ways to sign in" trigger, badge text. Usually Slate Grey Text.

### Named Rules
**The Tight Heading Rule.** Every semibold heading uses tight tracking (-0.025em) and `text-balance`; body text never does.

**The Muted Half Rule.** The wordmark and the hero headline split into a foreground half and a Slate Grey half. Emphasis by subtraction, not by colour or size.

## Layout

- **Base unit:** 4px (Tailwind spacing). Page gutter is 16px (`px-4`) at every width.
- **Containers:** 1152px (`max-w-6xl`) for the sticky header and landing sections; 1024px (`max-w-5xl`) for settings screens (Integrations, Sign-in settings); 768px (`max-w-3xl`) for the chat column and composer; 400px for the sign-in card. Running prose inside a container is capped near `max-w-lg` to `max-w-2xl`.
- **Header:** sticky, 48px in the app and 56px on the landing page, translucent white (`background/80`) with `backdrop-blur-md` and a 60% hairline underneath. On phones the app's nav scrolls sideways rather than wrapping.
- **Rhythm:** app screens pad 24px top and bottom (`py-6`); panels pad 20px; the sign-in card pads 24px; form fields stack at 20px (`FieldGroup`) and tighten to 12–16px inside a single form. Landing sections breathe at 80px, 112px from `sm`.
- **Sign-in page:** a centred column on a Fog ground at 50%. Wordmark top-centre, the card at roughly 9vh below, the "By Cloudastra." sign-off pinned to the bottom.
- **Breakpoints:** Tailwind defaults; `sm` (640px) is where most two-up and inline layouts switch on, `md` (768px) reveals the landing nav, `lg` (1024px) splits the hero into two columns.

## Elevation & Depth

Hybrid, flat by default. Panels, tiles and integration cards are outlined in a 70% hairline with no shadow. A soft, two-layer, low-opacity shadow lifts exactly one element per screen: the element the visitor acts on next. Menus and dialogs keep shadcn's stock `shadow-md` / `shadow-lg`, and the active tab gets `shadow-sm`.

### Shadow Vocabulary
- **Auth card** (`box-shadow: 0 1px 3px rgb(0 0 0 / 0.06), 0 12px 32px -12px rgb(0 0 0 / 0.14)`): the sign-in / create-workspace card. It has no border; the shadow alone separates it from the Fog ground.
- **Composer rest** (`box-shadow: 0 2px 20px -6px rgba(0,0,0,0.12)`) and **Composer focus** (`box-shadow: 0 4px 28px -8px rgba(0,0,0,0.2)`): the chat input, which deepens on focus alongside its border darkening to foreground at 25%.
- **Showcase** (`box-shadow: 0 20px 60px -24px rgba(0,0,0,0.25)`): the landing page's product preview.

### Named Rules
**The One Lifted Thing Rule.** A screen gets at most one custom shadow, and it goes on the primary interaction surface. Everything else is a bordered, flat plane.

## Shapes

The radius scale derives from one base, `--radius: 0.625rem` (10px), multiplied: `{rounded.md}` 8px (0.8x), `{rounded.lg}` 10px, `{rounded.xl}` 14px (1.4x), `{rounded.2xl}` 18px (1.8x), `{rounded.3xl}` 22px (2.2x).

- **10px** for anything you click or type into: buttons, inputs, tab rails, the route pass, inline notes and callouts.
- **8px** for tab triggers nested inside a 10px rail (inner radius smaller than outer).
- **14px** for the sign-in card and the dashed inner frame of the landing data-flow diagram.
- **18px** for app panels, integration cards, landing feature tiles, chat bubbles and skeleton placeholders standing in for panels.
- **22px** for the chat composer and the landing CTA band, the two largest single surfaces.
- **Full pill** for badges, avatars, status dots, filter chips, the model picker and every landing-page button.

Borders are 1px, drawn at reduced opacity. Dashed borders appear once, to mark a boundary ("Inside your infrastructure") rather than a container.

## Components

### Buttons
Quiet and solid; they darken by fading, not by changing colour.
- **Shape:** gently rounded (10px), 32px tall by default, 28px `sm`, 36px `lg`; icon-and-label gap 6px.
- **Primary:** Graphite Ink fill, Paper Ink text, 14px medium. Hover drops to 80% opacity.
- **Outline:** white fill, Hairline border; hover fills Fog. Used for every "Continue with <provider>" and for secondary actions next to a primary.
- **Secondary:** Fog fill, ink text; hover mixes 5% foreground into the fill. Used when a primary already exists above it (the password form under email-first sign-in).
- **Focus / Active:** border turns Focus Grey with a 3px halo at 50%; pressed buttons nudge down 1px. Disabled is 50% opacity.
- **Marketing pill:** the landing page reuses the same variants with a full pill radius and wider padding (24px at `lg`).
- **Loading:** a spinning Lucide `Loader2` replaces the leading icon; the label stays.

### Chips / Badges
- **Badge:** full pill, 20px tall, 12px medium text. Default is Graphite Ink; outline variant is a hairline pill with muted text (the landing hero's claim pill).
- **Chips:** Fog pill, 12px text, used for selected folders and filters in Integrations.

### Cards / Containers
- **Panel (app):** 18px radius, white, 70% hairline, 20px padding, section title at 16px medium. No shadow.
- **Auth card:** 14px radius, white, no border, the Auth card shadow, 24px padding, 400px wide.
- **shadcn Card:** 14px radius with a 10% foreground ring and 16px internal spacing, where the stock primitive is used.

### Inputs / Fields
- **Style:** 32px tall, 10px radius, Hairline stroke, transparent fill, 10px horizontal padding.
- **Focus:** stroke turns Focus Grey plus a 3px halo at 50%.
- **Error:** stroke turns Signal Red with a 20% red halo; the `FieldError` line below is 14px red. Labels sit 8px above the input; descriptions are 14px Slate Grey.
- **Disabled:** 50% opacity on a faint input-tinted fill.
- Fields are the shadcn `Field` / `FieldLabel` / `Input` stack. Two older screens still carry bare native controls (the chat composer's `<textarea>`, an `<input>` in the docs view); they are exceptions, not the pattern.

### Navigation
- **App header:** text-only tabs at 14px, Slate Grey at rest, foreground on hover and when active. The active tab carries a 1px foreground underline flush with the header's bottom edge. Account and model controls sit at the right as a ghost pill and a 28px avatar.
- **Landing header:** wordmark, 24px-spaced text links in Slate Grey (hover foreground), and a pill "Sign in" button.
- **Tabs (in-content):** a Fog rail with 3px padding; the active trigger becomes a white 8px chip with `shadow-sm`.

### Route pass (signature)
The sign-in surface's signature move: as a visitor types a work email, a strip directly under the field resolves the domain and says, before they leave the page, which **Workspace** they are entering, what they will **Sign in with**, and where their **Roles** come from.
- **Frame:** 10px radius, Hairline border, Fog at 40% fill, 14px text.
- **Idle:** a single muted hint line ("Type your work email to see your workspace and how you'll sign in.").
- **Looking:** three segments, each with a 12px Slate Grey label and a Skeleton bar where the value will land.
- **Resolved:** the frame turns white and its border darkens to foreground at 15%. Values appear in 14px medium with a 300ms fade-and-rise (motion-safe only); the method segment carries the provider's own mark at 14px. The primary button below rewrites itself to "Continue with <method>" and takes the same mark.
- **No workspace:** the strip becomes a short sentence ("acme.com doesn't have a workspace here yet.") and, when sign-up is on, a small outline button that carries the suggested name into the Create a workspace tab.
- **Layout:** three columns (1fr / 1.3fr / 1fr) split by hairline dividers from `sm`; on phones the segments stack as label-left, value-right rows divided horizontally. Values truncate with a title tooltip on phones and clamp to two lines from `sm`.
- **Accessibility:** the strip is an `aria-live="polite"` region; lookup is debounced 350ms and failure degrades to "Continue still works."

### Numbered steps
Used in Create a workspace: a 24px outlined circle with a 12px medium tabular numeral in Slate Grey, beside a 14px medium step title and a 14px muted description. The first step can hold its own input inline.

## Do's and Don'ts

### Do:
- **Do** build every control from the shadcn primitives in `frontend/src/components/ui` (`Button`, `Input`, `Field`, `Tabs`, `Badge`, `Select`, `Textarea`) on the tokens in `frontend/src/index.css`.
- **Do** keep hierarchy achromatic: Graphite Ink and Near-Black for emphasis, Slate Grey for everything secondary.
- **Do** outline flat planes with the Hairline at 70% and reserve the soft layered shadow for the one element the visitor acts on next.
- **Do** use 10px radius for interactive elements and 18px for app panels; pills only for badges, avatars, chips and landing-page buttons.
- **Do** set one Title (24px semibold, tight tracking) per app screen and 16px medium for panel titles.
- **Do** show a resolved state in place, next to the thing that caused it (the route pass, sources under an answer), with motion behind `motion-safe`.
- **Do** use Lucide for interface icons and simple-icons paths for provider and integration marks.

### Don't:
- **Don't** introduce a brand hue for emphasis, links or active states; the palette has none.
- **Don't** add new raw Tailwind palette colours (`emerald-*`, `amber-*`, etc.). Existing success and warning uses are drift awaiting tokens, not a precedent.
- **Don't** put a shadow on every card; a screen with two lifted surfaces has lost its focal point.
- **Don't** use native `<input>`, `<select>`, `<textarea>` or date/month inputs.
- **Don't** add off-scale font sizes (`text-[11px]`, `text-[10px]`, `text-[15px]`); use Label (12px) or Body (14px).
- **Don't** rely on the `.dark` token set; dark mode is not switched on anywhere in the shipped app.
