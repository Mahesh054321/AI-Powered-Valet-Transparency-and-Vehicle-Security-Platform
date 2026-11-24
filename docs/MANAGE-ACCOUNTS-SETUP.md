# Manage Accounts — Admin dashboard (Render deployment)

This patch adds **Manage Accounts** at `/staff-accounts` and preserves existing
Render and Admin-bootstrap configuration.

## Apply in PowerShell (project root)

```powershell
Expand-Archive -Path "$env:USERPROFILE\Downloads\Valet-Manage-Accounts-Patch.zip" -DestinationPath . -Force
python -m compileall -q backend
git status
git add backend/services/auth.py backend/routes/auth.py backend/utils/security.py backend/tests/test_admin_account_management.py frontend/src/pages/StaffAccountsPage.jsx frontend/src/pages/StaffAccountsPage.test.jsx frontend/src/services/staffAccountService.js frontend/src/services/endpoints.js frontend/src/layouts/MainLayout.jsx frontend/src/routes/index.jsx frontend/src/main.jsx frontend/src/styles/pages/accounts.css docs/MANAGE-ACCOUNTS-SETUP.md
git commit -m "Add admin account management dashboard"
git push origin deploy/render
```

**Note:** `git add` covers only the files in this patch. It does not stage
`backend/.env` or modify Render's existing `render.yaml`.

Render automatically deploys pushed commits to services configured for auto-deploy.
If auto-deploy is disabled, use Render Dashboard > service > Manual Deploy >
Deploy latest commit for **both** `valet-api-demo-054321` and
`valet-web-demo-054321`.

When both deployments are `Live`, sign in as an Administrator and open:

https://valet-web-demo-054321.onrender.com/staff-accounts

## Available actions

- List/Search/Filter Customer, Valet, Manager and Administrator accounts (20/page).
- Create Customer, Valet or Manager accounts with unique email login IDs.
- Edit full name, login email, phone and role for non-admin accounts.
- Reset a non-admin account's password (never view existing passwords).
- Delete / Deactivate non-admin accounts, preserving their historical records.
- Reactivate previously deactivated accounts.
- Administrator accounts appear in the list but **cannot be edited or deleted**
  from this page; the internal UUID is always read-only.
- All writes require ADMIN authorization on the server and are audited.

## Notes

1. A `DELETE` here is **soft-delete** (`DEACTIVATED`). Account identifiers,
   ticket/vehicle relations and audit records remain in the database.
2. Changing an account password does **not** immediately invalidate all
   previously issued JWTs; active tokens expire according to the configured TTL.
   For high-security production use, implement server-side session invalidation.
3. No database migration is needed: the existing `users` table already stores
   the required fields and statuses.
4. No paid service is introduced; the current free Blueprint remains unchanged.
5. If the backend returns an error, check Render backend logs and the browser's
   Network tab. Do not paste bearer tokens or passwords into chats or screenshots.

## Verification performed

- Python `compileall` syntax check of backend completed.
- JSX/JS source parsed successfully using TypeScript parser.
- Full `npm run build` and PostgreSQL integration tests could not be run in
  this environment because Node dependencies and Flask/PostgreSQL are missing.
  Run `cd frontend; npm ci; npm run build; npm test` on your PC and check
  Render build and runtime logs before using the feature with real accounts.
