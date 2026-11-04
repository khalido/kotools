# Microsoft 365 integration (research, not built)

**Status: researched 2026-08-01, nothing implemented** (no `msal` or Graph code in `src/`). Goal: `ko` talks to Microsoft 365 with the regular work account (an ordinary member of the employer tenant, no admin rights) the way it talks to Google. Read mail, write drafts, and agentically read and edit Office files in OneDrive/SharePoint folders the account can already reach.

## The recipe

1. **App registration in my own free Entra tenant, marked multitenant.** Never the employer's. Every third-party M365 integration works this way: the app's home tenant doesn't decide which tenants' users can sign in. In portal.azure.com → App registrations (free, no subscription), set supported account types = "Any Microsoft Entra ID tenant (Multitenant)", platform = Mobile and desktop applications, redirect `http://localhost`, "Allow public client flows" = Yes, no client secret.
2. **Auth: MSAL Python, device-code flow.** `PublicClientApplication(client_id, authority=".../common")` + `acquire_token_by_device_flow()`. Device code beats interactive-browser: no redirect-URI dependency, it survives corporate Conditional Access quirks, and it works over SSH. Cache tokens with `SerializableTokenCache` in `~/.local/state/ko/msal_token_<account>.json` (0600), the structural twin of `google_auth.py` (per-account files, cached session getters).
3. **Library: `msal` + plain `requests`.** Not msgraph-sdk (generated, async-first, heavy). Not python-o365 (its own idioms break the "params match the upstream API" rule). Graph's raw REST is the thin layer.
4. **Scopes, start narrow** (mirroring the Google readonly/write sets). Readonly = `User.Read Mail.Read offline_access`. The write set adds `Mail.ReadWrite Mail.Send`. Files = `Files.ReadWrite` (own OneDrive) or `Files.ReadWrite.All` + `Sites.Read.All` for SharePoint libraries.

## The one real risk: tenant consent policy

Since mid-2025 Microsoft defaults tenants to managed consent: users self-consent only to low-impact scopes from **verified publishers**. An unverified personal app will likely hit **"Need admin approval"** for `Mail.Read` and everything above it on a locked-down tenant.

**Test it in one minute, at zero risk.** Register the app, request only `Mail.Read`, and sign in with the work account. The first-sign-in screen is the answer: clean consent, or "Need admin approval" (worst case a pending request; nothing is granted, nothing breaks). If blocked, use the tenant's admin-consent request workflow. A read-only mail scope from a personal productivity tool is an easy yes for most IT teams.

**Do not use the well-known first-party client IDs** (Azure CLI `04b07795-…`, Graph PowerShell `14d82eec-…`), even though they're pre-consented everywhere. That is a documented red-team technique, SOCs alert on it, and "no admin rights + an auth pattern that looks like credential theft" is the worst combination. Use a real registration and real consent.

## Mail via Graph — deltas vs the Gmail module

- `GET/POST /me/messages` (+ `/me/mailFolders/inbox/messages/delta` for sync). `POST /me/messages` creates a draft directly.
- **No labels.** One folder per message (`mailFolders`). The Gmail label model doesn't port; design around folders.
- **Reply threading is server-side.** `POST /me/messages/{id}/createReply` builds headers and quoting (the Gmail module does this by hand). PATCH the draft, then `POST …/send`.
- Search is `$search` (KQL-ish), not Gmail operators. No pass-through.
- 429s carry `Retry-After`. Wrap with backoff like `raise_for_status`.

## Files/Office via Graph

- Addressing: `/me/drive/root:/path:` for OneDrive. SharePoint needs site-id → drive-id first (`GET /sites/{hostname}:/sites/{path}:` → `/sites/{id}/drives`).
- Any file: download with `GET …/content`; upload ≤4MB with `PUT …/content`, larger via `createUploadSession` (resumable, `conflictBehavior`).
- **Excel is the safe path for an agent editing a shared spreadsheet.** It has a real REST API for cell/range edits: `/workbook/worksheets/{name}/range(address='A1:B2')` GET/PATCH, tables, `usedRange`. Batch edits inside a workbook session (`createSession {persistChanges:true}`, ~5-min idle timeout). Expect an occasional 423 locked when the file is open in Excel elsewhere.
- **Word has no content API.** Download → `python-docx` → re-upload. That replaces the whole document and can clobber concurrent editors. Check eTag/lastModified before download and again before upload, refuse on drift, and keep a backup copy. Don't call it safe for actively co-edited docs; Excel is.

## If the tenant blocks consent: the sync-folder bridge

Context: the UTS account is restricted and probably can't grant app consents. The work content is mostly non-proprietary (open-source code, robot plans, paper summaries), and the headline use case is "write a summary of this paper into this document."

Delegated OAuth **is** the nibbleedge model: the app acts as the signed-in user, bounded by what that user can already touch. No admin keys, no service principals with standing access. A locked tenant doesn't block the permission model. It blocks *consent to an unverified app*. So the ladder is:

1. **Run the 1-minute consent test anyway** (above). If UTS allows user consent, everything works and this section is moot.
2. **Files without any API: OneDrive sync as the bridge.** The OneDrive client already runs with the user's own permissions. Sync the relevant folders (SharePoint libraries too, via "Add shortcut to OneDrive" / Sync). The agent reads and writes **plain local files** (docx via python-docx, xlsx via openpyxl, markdown as markdown), and the sync client ships them up as ordinary user edits from the user's machine. No app registration, no consent screen, no new credential surface. This covers the paper-summary use case completely. Limits: only synced folders; concurrent co-editing produces conflicted copies (no merge); nothing for mail.
3. **Mail, if it matters:** Graph is the only clean path. File the admin-consent request for `Mail.Read` and wait. Do not auto-forward work mail to a personal account to dodge this. Most orgs treat that as a policy violation, and it's visible.
4. **Keep the truly agentic workspace where the tooling already works** (personal Google, local repos) and land only finished artifacts into UTS via the synced folder. For mostly-public research content, the fight for tenant API access may not be worth having.

## Prior art

python-o365 (MSAL-based, decent coverage, own abstraction), msgraph-sdk (official, heavy), pnp/cli-microsoft365 (Node; the best UX reference for a personal M365 CLI, device-code by default). None beats msal+requests for this codebase's philosophy.
