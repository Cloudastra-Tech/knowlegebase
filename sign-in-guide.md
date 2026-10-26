# Connecting your identity provider

For a workspace's **admin**: how to let your people sign in to the knowledge
assistant with your company's own identity provider (Okta, Microsoft Entra ID,
Google Workspace, Keycloak, or any other OpenID Connect or SAML provider), and
how their roles are decided.

Everything here is on the **Access** screen (top bar), which only admins see.
It has two tabs: **Sign-in** (domains, providers, rules, recent sign-ins) and
**Members** (everyone's role and access).

## 0. Create your workspace (new companies)

If your company isn't on this server yet, open the sign-in page, choose
**Create a workspace**, name it, and **Continue with Google Workspace** using
your company Google account. Google tells us which company domain that account
belongs to, so the domain is verified for your new workspace straight away and
you become its admin; you land on **Access** with a short **Set up your
workspace** checklist. Your colleagues with an email on that domain can then
sign in with **Continue with Google Workspace**; add your own provider below if
you use one. A personal Gmail account, or an address on a secondary domain,
can't create a workspace; nor can a domain that already has one.

Whoever runs the server turns sign-up on with `WORKSPACE_SIGNUP=on` (it needs
the server's Google client); it's off by default, so a server run for one
company stays that company's. With `WORKSPACE_SIGNUP_CODES` (comma-separated)
it's invite-only: the founder also enters a code, and each code creates one
workspace.

Before you sign in, typing your email shows whether your domain has a workspace
here, how it signs you in and where roles come from, but never the
workspace's name.

The order of work:

1. [Verify your email domains](#1-verify-your-email-domains)
2. [Add your provider](#2-add-your-provider) (it starts **off**)
3. [Test it, then turn it on](#3-test-it-then-turn-it-on)
4. Optional: [roles from groups](#4-roles-from-groups), [require single sign-on](#6-require-single-sign-on), [sign out at the provider](#7-sign-out-at-the-provider)

---

## 1. Verify your email domains

People can only sign in with an email on a domain your workspace has proved it
owns. Proving it is what stops anyone else's provider from signing people in as
your staff.

1. **Access → Sign-in → Email domains**: type the domain (e.g. `example.com`)
   and press **Add domain**.
2. The screen shows a **TXT record**. Add it at your DNS provider (Cloudflare,
   Route 53, GoDaddy, Google Domains, …):

   | Type | Name (host) | Value |
   |---|---|---|
   | TXT | `_knowledge-assistant.example.com` | `kb-verify=…` (copy it from the screen) |

   Some DNS providers want only the part before your domain as the name
   (`_knowledge-assistant`).
3. Press **Check DNS**. New records can take a few minutes to appear. Once it
   says **Verified**, you can remove the record if you like; the domain stays
   verified.

Add every domain your people's emails use, one at a time (a subdomain such as
`eu.example.com` is a separate domain). A domain can be verified by one
workspace only.

## 2. Add your provider

**Access → Sign-in → Identity providers → Add provider**. Pick **OpenID
Connect** or **SAML**: use whichever your provider makes easier; both are
equally secure here.

The top of the dialog shows **this app's addresses**, which your provider asks
for when you create the app there. Create the app at your provider first (steps
below), then come back and fill in the rest. New providers are added **off**.

### OpenID Connect

You give us: **Issuer URL**, **Client ID**, **Client secret**. Your provider
needs: our **Redirect URI** (a "Web" app, authorization code flow).

We check the issuer straight away: its `/.well-known/openid-configuration` must
load and name the same issuer.

### SAML

You give us your provider's metadata, one of three ways:
**Metadata URL** (best: it's fetched now, and again every day, so a new signing
certificate is picked up by itself), **Metadata XML** (paste the file), or
**Enter by hand** (entity ID, sign-in URL, signing certificate). Your provider
needs: our **Entity ID** (also called audience or identifier) and **ACS URL**
(single sign-on URL, reply URL), or simply our **SP metadata URL**, which has
both and our certificate. We sign our sign-in requests when your provider's
metadata asks for it.

Ask your provider to send the person's **email**, either as the NameID (format
*EmailAddress*) or as an attribute called `email` or `mail` (Entra's standard
email claim also works). If it's under another name, put that name in **Email
attribute**.

### Provider by provider

The menu names below are as of 2026; providers rename things now and then.

**Okta, OpenID Connect.** Applications → Create App Integration → *OIDC*,
*Web Application*. Sign-in redirect URI: our Redirect URI. Assign the people or
groups who should have access. Copy the Client ID and secret. Issuer: your
authorization server's issuer, shown under Security → API → Authorization
Servers (for example `https://your-org.okta.com/oauth2/default`).

**Okta, SAML.** Applications → Create App Integration → *SAML 2.0*. Single
sign-on URL: our ACS URL. Audience URI: our Entity ID. Name ID format:
*EmailAddress*; Application username: *Email*. Then, on the app's Sign On tab,
copy the **Metadata URL** into our dialog.

**Microsoft Entra ID, OpenID Connect.** App registrations → New registration,
*single tenant*, redirect URI (Web): our Redirect URI. Certificates & secrets →
New client secret. Token configuration → Add optional claim → ID → `email`.
Issuer: `https://login.microsoftonline.com/<your tenant ID>/v2.0`. Entra doesn't
send an "email verified" flag; that's fine, since only your verified domains
get in.

**Microsoft Entra ID, SAML.** Enterprise applications → New application →
Create your own → *non-gallery*. Single sign-on → *SAML*. Identifier: our Entity
ID. Reply URL: our ACS URL. Copy the **App Federation Metadata Url** into our
dialog. Assign users and groups under Users and groups.

**Google Workspace, SAML.** Admin console → Apps → Web and mobile apps → Add
app → *Add custom SAML app*. Download the IdP metadata and paste it as
**Metadata XML**. ACS URL and Entity ID: ours. Name ID: format *EMAIL*, value
*Primary email*. Turn the app on for your organisational units. (If this server
also shows *Continue with Google*, your people can use that instead, with no
setup.)

**Keycloak.** OpenID Connect: a *confidential* client with our Redirect URI;
issuer `https://<host>/realms/<realm>`. SAML: Clients → Import client, then load
our SP metadata URL; give it a mapper that sends `email`.

**Anything else** (Auth0, OneLogin, JumpCloud, ADFS, …): the same values under
their names. For OpenID Connect you need an issuer that publishes
`/.well-known/openid-configuration`; for SAML, the HTTP-Redirect binding.

## 3. Test it, then turn it on

Press **Test sign-in** on the provider. A window opens at your provider; sign
in there. The window closes itself and the screen shows **what would happen**
("Pat would sign in as HR") or **why not**, and everything the provider sent.
Nobody is signed in and nobody is added. It works while the provider is off.

Your provider may sign you in as yourself without asking, if you're already
signed in there. To test as someone else, sign out at your provider first.

When it looks right, press **Turn on**. From then on, anyone who types an email
on your verified domains on the sign-in page goes to your provider. With more
than one provider on, they choose.

## 4. Roles from groups

Everyone signs in as **Public** (chat over public documents) unless something
gives them more:

| Role | Can |
|---|---|
| Public | chat over public documents |
| HR | also HR documents, the Documents and Integrations screens |
| Admin | also this Access screen |

To set roles from your provider's groups, open the provider's **⋯ → Roles from
groups**:

- **Claim / attribute with the groups**: where your provider puts them.
  OpenID Connect usually `groups`; nested claims work too, like Keycloak's
  `realm_access.roles`. SAML: the attribute name, such as `groups`, `Role`, or
  Entra's `http://schemas.microsoft.com/ws/2008/06/identity/claims/groups`.
  **Test sign-in shows exactly what your provider sends**, so look there first.
- **Rules**: *group* gets *role*. Someone in several groups gets the highest.
  Group names are matched without regard to case. Entra sends group **object
  IDs**, so use those as the names.

Groups are read **each time someone signs in**: someone you add to or remove
from a group gets the new role at their next sign-in. To change it straight
away, use the Members tab.

**One provider sets roles.** The provider you give rules to becomes the
workspace's **role source**; its card says *Sets roles (role source)*, the
others *Doesn't set roles*. Signing in through another provider still works,
but leaves the person's role as it is, so it never depends on which button they
click. To take roles from a different provider, give that one rules: the dialog
asks *Roles currently come from X. Switch to Y?* When you switch, clear the
source's rules or delete it, the roles its groups gave are gone straight away,
and the new source's apply at each person's next sign-in through it. Roles set
on the Members tab stay. None of this is allowed if it would leave the
workspace with no admin: make someone admin on the Members tab first.

**Admin from groups expires.** Groups only refresh at a sign-in through the role
source, so someone removed from the admin group at your provider keeps admin
here until then. **Sign-in rules → Expire admin from groups** (30 days by
default; 0 = never) drops admin from groups that old to what the rest of their
groups give. Roles set on the Members tab don't expire, and the last admin
keeps it: they're marked on the Members tab, and *Recent sign-ins* shows a
warning.

Your provider has to be set to send groups: in Okta, a groups claim (or a SAML
group attribute statement); in Entra, Token configuration → *Add groups claim*
(people in more than 200 groups get no groups claim from Entra, so use app
roles and the `roles` claim instead); in Google Workspace, *Group membership*
in the SAML app's attribute mapping.

## 5. Members

**Access → Members** lists everyone who has signed in or been given a role.

- **Role**: *From groups* follows your provider's groups (the default). Picking
  Public, HR or Admin **overrides** the groups for that person until you set it
  back to *From groups*. The text under the picker says where the role comes from,
  and for a role from groups, when the role source last sent them (*Groups
  updated 12 d ago*).
- **Turn access off**: they can't sign in, and they're signed out on their next
  click. Use this to keep someone out. **Remove** only deletes their record:
  if your provider still lets them sign in, they come back as a new member.
- You can't change your own access; ask another admin. So there's always at
  least one admin.

Role and access changes apply on the person's next click, with no sign-out needed.

## 6. Require single sign-on

**Access → Sign-in → Sign-in rules → Require single sign-on**: nobody but your
admins can sign in with a password, and anyone signed in with one is signed out.
Admins keep their password as the way in if your provider ever breaks, so keep
at least one admin with a password.

It can only be turned on once you have a verified domain and a provider that's
on.

## 7. Sign out at the provider

By default, signing out of the knowledge assistant signs you out of it only.
For OpenID Connect providers that support it, **Edit** → *Also sign out at …*
ends the provider session too. Note that this signs people out of **every**
app that uses that provider. Register the **Sign-out redirect URI** it shows
at your provider (Okta: *Sign-out redirect URIs*; Entra: add it as another *Web
redirect URI*; Keycloak: *Valid post logout redirect URIs*). SAML single logout isn't
supported.

## 8. Provisioning (SCIM)

With SCIM, your identity provider adds people before their first sign-in,
keeps their names and groups current, and turns them off here the moment it
does there (they're signed out everywhere at once). **Access → Sign-in →
Provisioning (SCIM)**:

1. **New token**, and copy it: it's shown once. Give your provider the token
   and the **SCIM base URL** shown there (Okta: *SCIM connector base URL*, with
   *HTTP Header* authentication; Entra ID: *Tenant URL* and *Secret token*).
   The section's setup notes have the steps for both, with links to their docs.
2. Only emails on your verified domains, or addresses you've invited, can be
   provisioned; anything else is refused and listed in *Recent SCIM events*.
   People sign in with their **work email**, so map it (Entra: keep
   `emails[type eq "work"].value`; `userName` may be a UPN).
3. **Roles from groups** there makes SCIM the role source: the groups your
   provider pushes set roles as they change, and admin from them doesn't
   expire. As with providers, a workspace has one role source.
4. Deleting someone at the provider turns them off now and anonymises their
   name and email after `SCIM_RETENTION_DAYS` (default 30); the row and the
   audit log stay. No SCIM change can remove the workspace's last admin: the
   provider gets an error saying so.

Revoke a token from the same section; the provider is refused from its next
request. Every change SCIM makes is in **Access → Activity** (actor *scim*).

## Troubleshooting

**Access → Sign-in → Recent sign-ins** shows each sign-in and refusal, with the
reason and how to fix it. **Test sign-in** shows what the provider sent, and the
same reason.

When a sign-in fails, the person only sees a general message and a
**reference** such as `K7Q2-9XMA` ("Reference: K7Q2-9XMA" under the error). The
same reference is on that attempt in Recent sign-ins (and in the server log), so
when someone reports a problem, ask for it. A provider whose latest sign-ins all
failed shows **Failing** on its card, with since when, why and how many attempts;
it clears at the next sign-in that works (a passing Test sign-in counts too).
Cancelling at the provider and other things only the person can cause don't make
a provider fail.

Each reason has a code (in `sso_errors.py`):

| Code | Reason | What to do |
|---|---|---|
| `invalid_client` | Client ID or secret is wrong | Copy the client ID and secret from your provider's app again (**Edit**). A secret that expired there needs a new one. |
| `invalid_grant` | Redirect URI doesn't match what's registered, or the sign-in took too long | Register exactly the Redirect URI under **Setup details** at your provider. |
| `unauthorized_client` | The provider's app isn't allowed to sign people in this way | Turn on the authorization code flow ("standard flow") for the app. |
| `invalid_scope` | The provider doesn't allow the `openid`, `email` and `profile` scopes | Allow them for the app at your provider. |
| `consent_required` | The provider needs an admin to approve this app first | Entra ID: **Grant admin consent** for the app. |
| `access_denied` | Cancelled at the provider | Nothing, if they cancelled. Otherwise check they're assigned to the app. |
| `provider_unreachable` | Couldn't reach the provider | Check the provider is up and its issuer URL is right. |
| `token_invalid` | The provider's answer couldn't be verified (issuer, audience or signature) | The issuer URL and client ID here must match your provider's app exactly. |
| `state_mismatch` | The sign-in was finished in another browser or tab, or took too long | Nothing: start again from the sign-in page. |
| `provider_error` | The provider refused the sign-in | Look for the attempt in your provider's sign-in logs. |
| `settings_unreadable` | This provider's saved settings can't be read | The server's encryption key changed: **Edit** and enter the client secret again. |
| `saml_signature` | The provider's signing certificate changed | Added by **Metadata URL**, a new certificate is picked up by itself (the metadata is fetched again daily, and when a signature doesn't match: "Certificate updated automatically" in Recent sign-ins); if it still fails, click **Refresh metadata**. Pasted or typed in, it can't be: the card warns 30 days before the certificate expires; give the new one with **Edit**. |
| `saml_unsigned` | Neither the response nor the assertion is signed | Make your provider sign the assertion or the response. |
| `saml_unsolicited` | Sign-in started at the provider's app launcher | Start from our sign-in page; hide the app's tile, or make it a bookmark to our sign-in page. |
| `saml_unknown_request` | The answer was to a sign-in that expired, was already finished, or was started for another provider | Nothing, if it's now and then: start again. |
| `saml_expired` | Clocks are more than 2 minutes apart, or the answer took too long | Check your provider's clock. |
| `saml_audience` | The answer was meant for another app (audience or destination doesn't match) | Your provider must have this app's entity ID and ACS URL exactly as under **Setup details**. |
| `saml_issuer` | The answer came from a different identity provider | Refresh the metadata, or check its entity ID (**Edit**). |
| `saml_no_email` | The answer had no email address | Send it as the NameID or an attribute, and set **Email attribute** if it has another name. |
| `saml_refused` | The provider refused the sign-in (for example, they aren't assigned to the app) | Check the assignment and your provider's sign-in logs. |
| `saml_replayed` | The same answer was sent twice | Nothing: start again from the sign-in page. |
| `saml_invalid` | The provider's answer couldn't be read | Run Test sign-in to see what the provider sends. |
| `unverified` | The provider didn't send a verified email address | OpenID Connect: the `email` scope / claim; SAML: NameID or an `email` attribute, or set **Email attribute**. |
| `not_allowed` | Their email isn't on a verified domain of this workspace, or the account belongs to another workspace | Verify that domain (step 1); the email must be on it exactly. Another workspace: contact support. |
| `not_workspace` | A personal or guest account, not a work one | Sign in with the work account. |
| `disabled` | Their access has been turned off | Members → turn their access back on. |
| `sso_required` | Single sign-on is required, so their password can't be used | Sign in with single sign-on. |
| `sso_failed` | Something went wrong on this server | Find the reference in the server log. |
| `workspace_exists` | Sign-up: that company domain already has a workspace | Sign in instead, or ask its admin. |
| `account_exists` | Sign-up: the founder is already a member of a workspace | Sign in instead. |
| `signup_domain` | Sign-up: the email isn't on the Google Workspace's primary domain | Use an account on the primary domain. |
| `signup_off` | Sign-up is off on this server | Ask whoever runs it (`WORKSPACE_SIGNUP=on`). |
| `invite` | Sign-up: the invite code isn't valid, or was already used | Ask whoever runs the server for a code (`WORKSPACE_SIGNUP_CODES`). |

Roles don't follow groups? Test sign-in: is the group claim there, under the name
in **Roles from groups**? Groups apply at the next sign-in, and only through
the role source.

Warning "they're the only admin, so they keep it": their admin comes from groups
the role source hasn't sent in a while. Make someone else admin (Members), or
have them sign in through the role source.

**Needs review** on a SAML provider, or "Metadata refresh failed": the metadata at
its URL now names another entity ID, or a sign-in URL on another host, so it
wasn't applied (the old one still works). If the change is right, **Edit** →
*Metadata URL* → Save. "Refresh failed": the URL didn't answer with valid
metadata; it's tried again within the hour.
