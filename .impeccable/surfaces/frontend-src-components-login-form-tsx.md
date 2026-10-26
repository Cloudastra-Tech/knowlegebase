---
version: 1
slug: "frontend-src-components-login-form-tsx"
primary_target: "frontend/src/components/login-form.tsx"
related_targets: ["frontend/src/components/login-page.tsx"]
---

# Sign-in and sign-up

Scope: the sign-in page (`#login`) and the new "Create a workspace" sign-up path. Mode: Operate (the visitor completes a task: get into their company's workspace, or start one).

Audience and job: returning employees (most frequent: type work email, go), new employees (need to learn how their company signs in), and a company's first admin (create a workspace with Google Workspace, then set up sign-in). Constraints: no email sending exists; sign-up is behind a server setting; personal Gmail is never accepted.

## Direction contract

THESIS: Getting in should be legible. One card, two tabs, Sign in and Create a workspace; the email you type tells you, before you leave the page, which workspace you are entering, how it signs you in and where your role comes from. It refuses the category default: a stack of equal-weight "Continue with X" buttons and a password form that leave people guessing which is theirs.

OWN-WORLD: the app's own: white ground, near-black primary, neutral greys, Plus Jakarta Sans, shadcn Tabs, Field, Input, Button, Badge; 12px-radius card; Lucide icons and simple-icons brand marks.

STORY: a visitor types their email, watches the route pass resolve to their company and its method, presses Continue and lands in. A new company sees "No workspace for acme.com yet" and one click takes them to Create a workspace.

FIRST VIEWPORT: wordmark top-centre; card (max 400px) centred at the optical middle; Tabs at the card top; Work email field and full-width Continue as the primary action; the route pass, a segmented strip (Workspace · Sign in with · Roles), directly under the field, empty-state hint until a domain resolves; "Other ways to sign in" disclosure below (Google Workspace, username and password); help line under the card.

FORM: "One card, two tabs", position 7 of 7 on the ordered list, seed key daf4b0a0; signature move: the route pass, raised from the boarding-pass challenger.

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance
