# Microsoft 365 integration (research, not built)

**Status: researched 2026-08-01, nothing implemented.** Goal: `ko` talks to
Microsoft 365 with the regular work account (ordinary member of the employer
tenant, no admin rights) the way it talks to Google — read mail, write
drafts, and agentically read/edit Office files in OneDrive/SharePoint
folders the account can already reach.

## The recipe

1. **App registration in MY OWN free Entra tenant, marked multitenant.**
   Never the employer's. This is how every third-party M365 integration
   works — the app's home tenant is irrelevant to which tenant's users can
   sign in. portal.azure.com → App registrations (free, no subscription):
   supported account types = "Any Microsoft Entra ID tenant (Multitenant)",
   platform = Mobile and desktop applications, redirect `http://localhost`,
   "Allow public client flows" = Yes, no client secret.
2. **Auth: MSAL Python, device-code flow** —
   `PublicClientApplication(client_id, authority=".../common")` +
   `acquire_token_by_device_flow()`. Device code over interactive-browser:
   no redirect-URI dependency, survives corporate Conditional Access quirks,
   works over SSH. Token cache via `SerializableTokenCache` to
   `~/.local/state/ko/msal_token_<account>.json` 0600 — the exact structural
   twin of `google_auth.py` (per-account files, cached session getters).
3. **Library: `msal` + plain `requests`.** No msgraph-sdk (generated,
   async-first, heavy), no python-o365 (its own idioms — breaks the
   "params match the upstream API" rule). Graph's raw REST is the thin
   layer.
4. **Scopes, start narrow** (mirroring the Google readonly/write sets):
   readonly = `User.Read Mail.Read offline_access`; write set adds
   `Mail.ReadWrite Mail.Send`; files = `Files.ReadWrite` (own OneDrive) or
   `Files.ReadWrite.All` + `Sites.Read.All` for SharePoint libraries.

## The one real risk: tenant consent policy

Since mid-2025 Microsoft defaults tenants to managed consent — user
self-consent only for low-impact scopes from **verified publishers**. An
unverified personal app will likely hit **"Need admin approval"** for
`Mail.Read` and everything above it on a locked-down tenant.

**Find out in one minute, zero risk**: register the app, request just
`Mail.Read`, sign in with the work account. The first-sign-in screen IS the
answer — clean consent, or "Need admin approval" (worst case a pending
request, nothing granted, nothing broken). If blocked, use the tenant's
admin-consent request workflow; a read-only mail scope from a personal
productivity tool is an easy yes for most IT teams.

**Do NOT use the well-known first-party client IDs** (Azure CLI
`04b07795-…`, Graph PowerShell `14d82eec-…`) even though they're
pre-consented everywhere — that's a documented red-team technique, SOCs
alert on it, and "no admin rights + auth pattern that looks like credential
theft" is the worst possible combination. Real registration, real consent.

## Mail via Graph — deltas vs the Gmail module

- `GET/POST /me/messages` (+ `/me/mailFolders/inbox/messages/delta` for
  sync). `POST /me/messages` creates a draft directly.
- **No labels** — one folder per message (`mailFolders`). The Gmail label
  mental model doesn't port; design around folders.
- **Reply threading is server-side**: `POST /me/messages/{id}/createReply`
  builds headers/quoting for you (Gmail module does this by hand — Graph
  needs none of it). PATCH the draft, `POST …/send`.
- Search is `$search` (KQL-ish), not Gmail operators — no pass-through.
- 429s carry `Retry-After`; wrap with backoff like `raise_for_status`.

## Files/Office via Graph

- Addressing: `/me/drive/root:/path:` for OneDrive; SharePoint needs
  site-id → drive-id first (`GET /sites/{hostname}:/sites/{path}:` →
  `/sites/{id}/drives`).
- Any file: download `GET …/content`; upload ≤4MB `PUT …/content`, bigger
  via `createUploadSession` (resumable, `conflictBehavior`).
- **Excel is the star**: a real REST API for cell/range edits —
  `/workbook/worksheets/{name}/range(address='A1:B2')` GET/PATCH, tables,
  `usedRange`; batch edits inside a workbook session
  (`createSession {persistChanges:true}`, ~5-min idle timeout, expect
  occasional 423 locked when the file's open in Excel elsewhere). This is
  the safe "agent edits a shared spreadsheet" path.
- **Word has no content API**: download → `python-docx` → re-upload. That's
  a whole-document REPLACE — it can clobber concurrent editors. Guard with
  eTag/lastModified check before download and again before upload, refuse
  on drift, keep a backup copy. Don't market it as safe for actively
  co-edited docs; the Excel path is.

## If the tenant blocks consent: the sync-folder bridge

Context: the UTS account is restricted and probably can't grant app
consents. The work content is mostly non-proprietary (open-source code,
robot plans, paper summaries), and the headline use case is "write a summary
of this paper into this document."

Key reframe first: delegated OAuth **is** the nibbleedge model — the app
acts as the signed-in user, bounded by what that user can already touch. No
admin keys, no service principals with standing access. What a locked
tenant blocks is not the permission model but *consent to an unverified
app*. So the ladder is:

1. **Run the 1-minute consent test anyway** (above). If UTS allows user
   consent, everything works and this section is moot.
2. **Files without any API: OneDrive sync as the bridge.** The OneDrive
   client already runs with the user's own permissions. Sync the relevant
   folders (SharePoint libraries too, via "Add shortcut to OneDrive" /
   Sync); the agent reads and writes **plain local files** — docx via
   python-docx, xlsx via openpyxl, markdown as markdown — and the sync
   client ships them up as ordinary user edits from the user's machine. No
   app registration, no consent screen, no new credential surface. Covers
   the paper-summary use case completely. Limits: only synced folders;
   concurrent co-editing produces conflicted copies (no merge); nothing for
   mail.
3. **Mail, if it matters**: Graph is the only clean path — file the
   admin-consent request for `Mail.Read` and wait. Do NOT auto-forward work
   mail to a personal account to dodge this; that's a policy violation in
   most orgs and visible.
4. **Keep the truly agentic workspace where the tooling already works**
   (personal Google / local repos) and land only the finished artifacts
   into UTS via the synced folder. For mostly-public research content, the
   fight for tenant API access may simply not be worth having.

## Prior art

python-o365 (MSAL-based, decent coverage, own abstraction), msgraph-sdk
(official, heavy), pnp/cli-microsoft365 (Node; best UX reference for a
personal M365 CLI, device-code by default). None beat msal+requests for
this codebase's philosophy.
