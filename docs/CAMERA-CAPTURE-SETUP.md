# Capture with Camera — Valet Platform

Adds an actual live browser-camera preview and a phone-camera fallback without removing gallery upload.

## Where the button appears

- Check-in → Step 4, each required drop-off angle: `Capture with Camera` (photo uploads immediately as before).
- Vehicle condition assessment → Capture photos (drop-off and pickup): `Capture with Camera` (photo joins the queue; submit set to upload).
- Valet Dashboard → Pickup photo upload, each angle: `Capture with Camera` (select images for comparison; confirm to upload).

## What it does

- Starts the camera on click, shows a preview, and allows **Take Photo**.
- **Switch Camera** toggles preferred front/back cameras on supported devices.
- **Use Phone Camera** invokes the native camera picker as fallback when a browser cannot start a live stream.
- Camera JPEGs are resized to a maximum edge of 1920 px to reduce upload size.
- Stops the camera stream when the dialog closes, the picture is captured, or the component unmounts.
- Existing photo upload validation, custody IDs, per-angle tagging, backend endpoints and permissions remain intact.
- Permissions are requested only when the user opens the camera. HTTPS or localhost is required for live camera preview in supported browsers. The hosted Render URL uses HTTPS.

## Install on top of existing project

Extract `Valet-Camera-Capture-Patch.zip` at project root, preserving file paths.

```powershell
Expand-Archive -Path "$env:USERPROFILE\Downloads\Valet-Camera-Capture-Patch.zip" -DestinationPath . -Force
cd frontend
npm ci
npm run build
npm test -- --run src/components/camera/CameraCapture.test.jsx
cd ..
```

If the build passes, commit only the camera changes:

```powershell
git add frontend/src/components/camera/CameraCapture.jsx frontend/src/components/camera/CameraCapture.test.jsx frontend/src/pages/CheckInPage.jsx frontend/src/pages/InspectionCapturePage.jsx frontend/src/pages/ValetDashboardPage.jsx frontend/src/styles/components.css docs/CAMERA-CAPTURE-SETUP.md
git commit -m "Add camera capture to vehicle photo workflows"
git push origin deploy/render
```

Check Render's frontend deployment status and test on a phone and/or laptop. Allow camera access on the browser prompt. The first browser camera request might require permission.

## Notes

- No backend code, database migration or new environment variables needed.
- Device-camera permission and available cameras determine whether live preview works.
- The fallback camera input accepts JPEG, PNG and WebP (the supported server formats). Some mobile devices may need to choose JPEG-compatible output.
- Render Free uploads are not guaranteed to survive service restarts: use persistent object storage before real production use.
- Automatic tests in the ZIP are available, but a full frontend build was not verified in the artifact-generation environment because the npm registry could not be accessed there.
