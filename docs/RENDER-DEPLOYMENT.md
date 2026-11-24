# Render deployment guide (React + Flask + PostgreSQL)

## What this package changes

- Adds a Docker build for `backend/` (Flask, OpenCV, scikit-learn).
- Keeps your Vite frontend as a separate Render Static Site.
- Adds a Render PostgreSQL service and a **paid persistent disk** for vehicle photos.
- Fixes GPS SSE queue dict unpacking, which otherwise raises a runtime error.
- Makes production JWT validation reject missing users and database outages.
- Leaves local development configuration intact.

## Before deployment: security

1. **Never push `backend/.env` from the uploaded ZIP to GitHub.** If that file
   was ever public, rotate the PostgreSQL password, JWT secret, admin password,
   and any other secrets, and clean Git history if needed. Removing a tracked
   file is not enough to remove secrets from earlier commits.
2. Do not upload `backend/storage/media` or real vehicle photos into public Git.
3. This app still accepts a bearer token in the GPS SSE URL query string and
   stores access tokens in JavaScript-accessible browser storage. Media routes
   also use guess-resistant but public URLs. Before real customer data/public
   production use, replace this with safer credentialed streaming and
   authorized/signed media access. This Blueprint is for a controlled demo.
4. Refresh-token rotation/renewal is not implemented in the inspected routes.
   Users may need to sign in again when access tokens expire.

## 1. Add files to your own repository

Copy `render.yaml` into the repository root and the new Docker files into
`backend/`. If you use the prepared source ZIP, it already contains the files.

```sh
# From the repository root
python -m compileall -q backend
cd frontend
npm ci
npm run build
```

## 2. Select Render resources (paid setup in render.yaml)

The supplied Blueprint config provisions **paid** services: Standard API
compute (2 GB RAM, currently approximately $25/month), a 1 GB persistent disk
(approximately $0.25/month), and a small PostgreSQL instance (approximately
$6/month). That is roughly **$31.25/month before taxes, bandwidth or other
charges** at the currently published rates. Confirm current pricing in Render
before deployment. Do not activate it if you do not
want to incur charges. The fixed names are examples: change all occurrences
of the API/frontend names and their HTTPS rewrite destinations together if
Render assigns different hostnames.

This project's photos are saved to `UPLOAD_FOLDER`, so the disk is mounted at
`/app/storage/media`. Without a persistent disk or object storage, uploads can
be lost after a restart or redeploy. Render Free web services cannot have a
persistent disk; Free PostgreSQL also has limited lifetime. Do not switch to
free plans without addressing those constraints.

## 3. Create the Blueprint

In the Render dashboard choose **New → Blueprint**, authorize the GitHub repo
and select the branch containing the files. Review charges and configuration
before applying. The API's JWT secret is auto-generated; database credentials
come from the managed PostgreSQL connection string.

Backend URL (example):
`https://valet-api-demo-054321.onrender.com`

Frontend URL (example):
`https://valet-web-demo-054321.onrender.com`

Check the **actual URLs** after creation. If either name changes, update
`CORS_ALLOWED_ORIGINS` on the API and both frontend rewrite destinations (for
`/api/*` and `/media/*`), then redeploy. Treat these as examples, not
pre-provisioned URLs.

The `render.yaml` runs migrations during the paid API pre-deploy step. It
**does not** reset databases or seed demo data automatically.

## 4. Create the admin account (one time only)

In Render API Environment, set `SEED_ADMIN_EMAIL` and a strong
`SEED_ADMIN_PASSWORD`, then use the Render shell for the API service:

```sh
python utils/scripts/init_db.py --seed
```

This may also seed demo data. Read `backend/database/seed.py` before use on
non-demo databases. Remove `SEED_ADMIN_PASSWORD` from the environment after
initialization if no longer needed.

## 5. Smoke checks

- API health: `https://<actual-api>.onrender.com/api/health`
- DB readiness: `https://<actual-api>.onrender.com/api/readyz`
- Frontend route refresh loads React instead of a Render 404.
- Login works; JWT expiry and logout behave as intended.
- Create vehicle/check-in, upload an image, then redeploy and verify it persists.
- Verify `/api/*` and `/media/*` rewrites (including image loading).
- Test live GPS SSE while sending an actual fix; watch for proxy buffering.
- Test the AI endpoints with synthetic model artifacts built into the image.

## Known limitations

- One Gunicorn worker is intentional: GPS broadcaster and rate limiting are
  in-memory, not shared across worker processes. With 8 threads, a few
  simultaneous SSE demo clients can connect, but larger traffic requires a
  Redis/event-bus design and proper resource sizing.
- The model build runs synthetic demo training; it is **not** real-world
  validated damage/security prediction.
- No Docker executable or Render credentials were available in this editing
  environment, so the image and deployment have **not been run on Render**.
- Paid persistent disk instances are not horizontally scalable; for a real
  multi-instance product, prefer private object storage with authenticated
  access instead of a local disk.
