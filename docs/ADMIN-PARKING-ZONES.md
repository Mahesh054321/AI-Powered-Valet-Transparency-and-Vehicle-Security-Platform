# Admin parking zones + Manage Accounts (Render Free)

This is a combined patch. It includes the earlier **Manage Accounts** feature so
it can be applied even if you have not yet extracted the old accounts ZIP.
The current `render.yaml`, bootstrap scripts and local `.env` are not included
or changed.

## Install in VS Code (PowerShell, project root)

```powershell
Expand-Archive -Path "$env:USERPROFILE\Downloads\Valet-Admin-Accounts-and-Parking-Zones.zip" -DestinationPath . -Force

python -m compileall -q backend
git diff --check
git status

git add backend/routes/auth.py backend/routes/geofences.py backend/services/auth.py backend/services/geofence.py backend/utils/security.py backend/tests/test_admin_account_management.py backend/tests/test_geofence_endpoints.py backend/tests/test_smart_geofence_security.py backend/tests/test_security_ml.py frontend/src docs/MANAGE-ACCOUNTS-SETUP.md docs/ADMIN-PARKING-ZONES.md

git commit -m "Add admin-only parking zone management and accounts"
git push origin deploy/render
```

If Git says `nothing to commit`, inspect `git status` to check which files were
extracted. Never run `git add .` if you have unreviewed secrets in the project.

Render should auto-deploy the backend and frontend. If auto-deploy is disabled,
select **Manual Deploy > Deploy latest commit** separately for both services.
Wait for **Live** before testing. The ZIP has not been pushed to GitHub for you.

## What the administrator can do

Open `https://valet-web-demo-054321.onrender.com/parking-zones` with an ADMIN
account, or use **Administration > Parking Zones** in the left sidebar.

- **View** configured parking areas on a map; click a row to focus its boundary.
- **Create Zone** by entering a unique zone code, name and vehicle capacity.
- **Circle or Rectangle:** click map to choose the centre, then enter radius or
  length/width/orientation. Click **Preview on Map**, then **Create Parking Zone**.
- **Polygon:** choose `Draw polygon on map`, click at least 3 corners in order.
  Undo and Clear are available. Click **Preview on Map**, then save.
- **Edit** the zone's code, name, description, capacity, GPS tolerance and
  geometry. Lowering capacity below occupancy is rejected.
- **Deactivate/Activate** to stop or restore future vehicle assignments. This
  preserves custody, audit and location history: it does not hard-delete a zone.
- All zone geometry and status changes are audited by the existing backend.

For staff accounts, open `/staff-accounts`: Admin can create, edit, deactivate,
reactivate and reset passwords for non-admin users (see
`docs/MANAGE-ACCOUNTS-SETUP.md`).

## Other roles

- Customers, Valet Operators and Managers continue to **view assigned zones and
  GPS tracking maps**, and staff may assign ACTIVE zones during check-in.
- **Configure Safe Zone** and the inline builder have been removed from every
  tracking screen, including Admin tracking. Configuration lives only in the
  Admin's separate Parking Zones screen.
- Backend create/preview/update/delete/activate/deactivate geofence endpoints
  require ADMIN. This applies even if someone crafts their own HTTP requests.
  Authenticated read-only zone endpoints remain available.
- This intentionally supersedes the former customer-created private safe-zone
  feature: customers can no longer create/edit their own boundaries.

## Validation and limitations

- Python files passed `compileall`.
- JSX files passed TypeScript JSX parser syntax checks.
- Existing/new tests were updated for administrator-only zone editing. Full
  PostgreSQL integration tests, `npm run build`, frontend Vitest and a live
  Render deployment were **not** run in this environment because the needed
  packages/DB were not available. Check Render build logs and test create/edit
  with test zones before using the site for real vehicles.
- No schema migration or paid Render resources are introduced.
- A changed boundary affects future GPS verdicts; past GPS verdicts are not
  recomputed automatically.

**Privacy:** Do not share passwords, JWT tokens, connection strings or Render
secret values in screenshots or logs.
