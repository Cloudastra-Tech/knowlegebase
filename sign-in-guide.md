# Connecting your identity provider

For a workspace's **admin**: how to let your people sign in to the knowledge
assistant with your company's own identity provider (Okta, Microsoft Entra ID,
Google Workspace, Keycloak, or any other OpenID Connect or SAML provider), and
how their roles are decided.

Everything here is on the **Access** screen (top bar), which only admins see.
It has two tabs: **Sign-in** (domains, providers, rules, recent sign-ins) and
**Members** (everyone's role and access).

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
**Metadata URL** (best: it's fetched now), **Metadata XML** (paste the file), or
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

Your provider has to be set to send groups: in Okta, a groups claim (or a SAML
group attribute statement); in Entra, Token configuration → *Add groups claim*
(people in more than 200 groups get no groups claim from Entra, so use app
roles and the `roles` claim instead); in Google Workspace, *Group membership*
in the SAML app's attribute mapping.

## 5. Members

**Access → Members** lists everyone who has signed in or been given a role.

- **Role**: *From groups* follows your provider's groups (the default). Picking
  Public, HR or Admin **overrides** the groups for that person until you set it
  back to *From groups*. The text under the picker says where the role comes from.
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

## Troubleshooting

**Access → Sign-in → Recent sign-ins** shows each sign-in and refusal, with the
reason. **Test sign-in** shows what the provider sent.

| What you see | What to do |
|---|---|
| "isn't a verified domain of this workspace" | Verify that domain (step 1). The person's email must be on it exactly. |
| "didn't send a verified email address" | Make the provider send an email (OpenID Connect: the `email` scope / claim; SAML: NameID or an `email` attribute, or set **Email attribute**). |
| "belongs to another workspace" | That email is already a member of another workspace. Contact support. |
| "Their access has been turned off" | Members → turn their access back on. |
| SAML: "Neither the response nor the assertions are signed", "Failed to verify signature" | Your provider must sign the assertion or the response, with the certificate in the metadata you gave us. If you rotated its certificate, update the metadata (**Edit**). |
| SAML: "Condition not OK" or an expiry error | The clocks are more than 2 minutes apart, or the answer took too long. Check your provider's clock. |
| SAML: "Unsolicited response" | Sign-ins must start from our sign-in page, not your provider's app launcher. |
| OpenID Connect: an `invalid_client` / redirect error at the provider | The client secret or our Redirect URI doesn't match what's registered there. |
| Roles don't follow groups | Test sign-in: is the group claim there, under the name in **Roles from groups**? Groups apply at the next sign-in. |
