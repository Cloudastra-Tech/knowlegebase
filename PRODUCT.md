# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

- **Employees of a company using it.** They sign in with their company account (single sign-on, or a password an admin gave them) and ask questions about their company's documents. What they can see depends on their role (Public, HR, Admin).
- **Workspace admins** (IT, HR, ops). They connect the company's tools (Google Drive, Notion, Outline wiki, uploads), set up sign-in (email domains, OpenID Connect / SAML providers, roles from groups, Require single sign-on) and manage members.
- **New companies** arriving to try it: one hosted instance serves many companies, each in its own workspace with its own sign-in. (Confirmed 2026-10-26.)

## Product Purpose

Knowledge assistant answers questions from a company's own documents, in plain language, with the exact sources cited under every answer. Success: people get a checkable answer instead of hunting through drives and wikis, and nobody sees a document their role doesn't allow.

## Positioning

Answers are grounded in the company's own documents and always cited; restricted content is filtered by permission before the AI ever sees it; sign-in and roles follow the company's own identity provider.

## Operating Context

- Sources: Google Drive, Notion, Outline wiki, uploaded PDF/text files; re-indexed automatically.
- Sign-in: per-workspace OpenID Connect and SAML providers found from the email domain; server-wide "Continue with Google" for Google Workspace accounts (personal Gmail is refused, and the company must have verified its domain); username + password for accounts an admin created; Require single sign-on leaves passwords to admins only.
- Live at https://ask.cloudastra.co (Cloudastra's own workspace).

## Capabilities and Constraints

- Multi-tenant: workspaces, verified email domains, per-workspace identity providers, one role-source provider per workspace.
- No email sending infrastructure yet (no verification or invite emails).
- **Open decision:** the landing page says "No shared, multi-tenant SaaS" and "Runs on your own server"; the product is now also offered as a multi-company hosted instance. The landing copy has not been reconciled.
- **Being built:** self-serve workspace sign-up for new companies (first admin, workspace, guided sign-in setup). Before this, workspaces were only created on the server.

## Brand Commitments

- Name: "Knowledge assistant" (wordmark "Knowledge" + muted "assistant"), by Cloudastra.
- Voice: plain, short, specific sentences; no hype. "Sign in", not "Login".

## Evidence on Hand

- Landing page claims and feature copy: `frontend/src/components/landing-page.tsx` (cited answers, permission-aware, never used for training, read-only connections, guardrails).
- Admin guide: `sign-in-guide.md`. No customer logos, testimonials or metrics exist; none may be invented.

## Product Principles

1. Every answer is checkable: show where it came from.
2. Access follows the company: its directory, its domains, its roles.
3. Say exactly what will happen and why, especially when refusing someone.
4. An admin can always get back in.
