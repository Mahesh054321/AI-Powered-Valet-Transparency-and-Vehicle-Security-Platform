# AI-Powered Valet Transparency and Vehicle Security Platform

## 1. Project Summary in Simple English

This project is a full-stack system built for valet parking and vehicle security. The basic problem is this: when a customer gives a car to valet staff, there is a gap in trust and visibility. The customer cannot see where the car is, whether it moved without permission, what condition it was in when dropped off, and whether it was damaged during the custody period.

This project solves that by creating a transparent audit trail:

- the car is checked into custody,
- location and movement are tracked,
- geofence rules detect suspicious movement,
- before/after car photos are compared using OpenCV,
- damage candidates are identified,
- the customer can see a condition report,
- management can verify or reject the findings,
- and every action is stored in an audit log.

In short, the project turns valet operations from a black box into a traceable and reviewable system.

---

## 2. Why This Project Exists

The real-world need:

- Valet staff may move, inspect, or handle the vehicle.
- The customer may worry about damage, theft, or unauthorized movement.
- Businesses want a record proving what happened during the custody period.
- Without digital tracking, everything depends on memory and informal notes.

This project creates a digital chain of custody:

- who checked in the car,
- what condition it was in,
- where it was located,
- whether it left the safe zone,
- whether any damage was detected,
- whether a dispute was raised,
- what the manager decided.

That is why the system is called “valet transparency and vehicle security”.

---

## 3. Architecture Overview

The project uses a classic layered architecture with a separation of concerns.

### Frontend
- React 19 + Vite
- React Router
- TanStack Query for API state
- Zustand or React context for auth/user state
- Leaflet + OpenStreetMap for maps

### Backend
- Flask REST API
- SQLAlchemy ORM with PostgreSQL
- JWT-based authentication
- role-based access control (RBAC)

### AI / Vision Layer
- OpenCV
- NumPy, Pillow, scikit-image
- custom vehicle damage detection pipeline

### Data and Security
- PostgreSQL database
- geofence checks using coordinate math
- audit trail for actions and disputes

The important idea is: frontend, backend, database, and AI are kept separate. One module does not do everything. This makes the project clean, scalable, and easy to test.

---

## 4. High-Level Project Flow

The project follows this flow:

1. Customer vehicle is registered.
2. Staff create a valet ticket and check in the vehicle.
3. Photos are captured during drop-off.
4. Customer or manager tracks the vehicle location.
5. GPS movement is checked against the geofence.
6. Pickup photos are captured.
7. OpenCV compares drop-off and pickup images.
8. Damage candidates are extracted.
9. Findings are displayed to the user.
10. Manager can confirm, reject, or dispute the result.
11. Audit logs record every action.

This is the full business lifecycle of the platform.

---

## 5. Folder-by-Folder Architecture Explanation

## 5.1 Root Files

### README.md
This is the main documentation and project overview. It explains setup, technologies, demo flow, and commands. It is useful for onboarding and presentation but not the actual logic.

### package.json
This controls the workspace scripts for both frontend and backend. It lets you run combined commands like:
- npm run dev
- npm run test:backend
- npm run test:frontend
- npm run build

### Makefile
Optional shortcuts for developers. It helps avoid typing long commands repeatedly.

---

## 5.2 Backend Architecture

### backend/app/__init__.py
This is the application factory. It creates the Flask app and wires together the pieces:

- settings
- logging
- database
- CORS
- routes
- middleware
- error handlers

This file is important because it is the single composition root. It keeps app setup centralized instead of spreading configuration across the codebase.

Why it matters:
- easier testing
- cleaner startup
- same configuration for local dev and production
- avoids duplicate app initialization mistakes

### backend/app/extensions.py
This initializes the database connection object and settings for SQLAlchemy.

### backend/app/error_handlers.py
This catches errors and returns a standard JSON response structure instead of raw Flask exceptions.

### backend/app/cors_config.py
This controls which frontend origins are allowed to call the API.

### backend/app/logging_config.py
This configures logs for application monitoring and debugging.

---

## 5.3 Backend Routes Layer

### backend/routes/__init__.py
This file mounts all route blueprints.

It registers:
- health checks
- media
- versioned API routes
- auth routes
- inspection routes
- GPS routes
- geofence routes
- alert routes
- retrieval routes

This is the API entrance point.

### backend/routes/health.py
Contains health endpoints such as:
- /api/health
- /api/readyz

These are used to check app health and database status.

### backend/routes/inspections.py
This file handles the vehicle condition workflow:
- capture drop-off photos
- capture pickup photos
- compare before/after
- verify damage detections
- dispute findings
- annotate notes
- resolve disputes

This is one of the most important route files for the project.

### backend/routes/gps.py
Handles GPS event ingestion and security telemetry flows.

### backend/routes/geofences.py
Lets the system create and manage safe zones or geofences.

### backend/routes/alerts.py
Manages alerts generated from unusual movement, route deviation, or location anomalies.

### backend/routes/retrieval.py
Handles retrieval requests and status transitions when a customer asks for the vehicle.

### backend/routes/auth.py
Handles login, token generation, validation, and role checks.

### backend/routes/media.py
Serves uploaded media files safely and keeps file access controlled.

---

## 5.4 Backend Services Layer

This is where business logic lives.

### backend/services/inspections.py
This is the service that does the real condition workflow:
- validate uploaded photos
- pair drop-off and pickup images by angle
- store photos
- trigger comparison
- assess damage candidates
- update inspection status
- resolve disputes

This file is a key link between the HTTP API and the AI vision pipeline.

### backend/services/geofence.py
Applies business logic around geofences:
- check if a vehicle is inside or outside a zone
- read GPS updates
- generate movement verdicts
- raise security alerts if needed

### backend/services/alerts.py
This handles alert lifecycle:
- create alert
- resolve alert
- acknowledge alert
- severity and escalation logic

### backend/services/retrieval.py
Handles the retrieval process:
- request vehicle by customer
- estimate time
- update custody status
- trigger notifications

### backend/services/vehicles.py
Handles vehicle registration, status updates, and basic lifecycle operations.

### backend/services/media.py
Stores uploaded images and serves them through a safe path. It also makes demo media generation easier.

---

## 5.5 Backend Models Layer

Models represent database tables.

### backend/models/user.py
Stores user records, login info, roles, and access permissions.

### backend/models/vehicle.py
Stores vehicle registration details such as:
- plate number
- make
- model
- color
- owner
- status

### backend/models/custody_session.py
This is a core model. It represents the time the car is in valet custody.

It contains statuses like:
- CHECKED_IN
- PARKED
- RETRIEVAL_REQUESTED
- READY_FOR_PICKUP
- CHECKED_OUT

This is where the custody lifecycle is tracked.

### backend/models/parking_zone.py
Represents a physical zone or safe zone where a car is allowed to be parked.

### backend/models/gps_event.py
Stores GPS points and movement data.

### backend/models/security_alert.py
Stores security alerts for unusual motion, geofence breach, or suspicious movement.

### backend/models/alert.py
A higher-level alert model used in the alert center and dashboards.

### backend/models/vehicle_inspection.py
Stores inspection records for vehicle condition checks.

### backend/models/damage_detection.py
Stores detected damage candidates, severity, confidence, and verification status.

### backend/models/audit_log.py
This is extremely important. Every action is recorded here.

Examples:
- vehicle checked in
- inspection compared
- finding confirmed
- dispute raised
- alert resolved

This makes the platform auditable and trustworthy.

---

## 5.6 AI and Vision Layer

This is the intelligence layer.

### backend/ai/__init__.py
This file describes the AI modules and their purpose.

### backend/ai/vision/preprocessing.py
This module loads images, validates them, handles orientation, resizing, and color-space conversion.

It ensures the two images are comparable before differencing.

### backend/ai/vision/alignment.py
This is the image alignment step.

Why needed:
When two photos are taken from slightly different angles or positions, the same panel may not align perfectly. Without alignment, the system may treat the whole car as changed.

This file uses OpenCV feature matching with ORB and homography to align the images.

### backend/ai/vision/compare.py
This is the main difference detection logic.

It does:
- convert images to LAB color space,
- compare luminance and chroma differences,
- compare edge gradients,
- suppress false positives,
- find connected regions of changed pixels,
- extract bounding boxes of possible dents, scratches, or changes.

This is the heart of the damage detection engine.

### backend/ai/vision/damage.py
This module scores the candidate regions and labels them as likely damage types.

It uses shape and color based heuristics to decide if a region looks more like:
- scratch
- dent
- rust / paint issue
- general damage

### backend/ai/vision/report.py
This creates the evidence visuals:
- before/after comparison image
- heatmap overlay
- annotated boxes
- labels and severity info

This is what the user sees on the condition report page.

### backend/ai/vision/classifier.py
This is the ML wrapper around the classical CV damage detection pipeline.

It can use a trained model if available, otherwise it falls back to the rule-based heuristic.

This is where the project follows a hybrid approach:
- classical computer vision for explainability
- optional trained model for classification

### backend/ai/vision/pipeline.py
This assembles the whole 13-step damage detection flow.

It is the top-level function that runs the entire comparison and returns the result.

---

## 6. How Damage Detection Works (OpenCV Explained Simply)

This is the most important technical concept in the project.

### Step 1: Capture the Images
The system stores a drop-off image and a pickup image for the same vehicle and same angle (front, rear, left side, right side, roof, etc.).

### Step 2: Load and Validate Images
The backend checks:
- is it really an image?
- is the file corrupted?
- is it too small?
- does the image match expected format and metadata?

### Step 3: Resize and Normalize
Images may come from different devices and have different resolutions. The system normalizes them so they are comparable.

### Step 4: Align the Images
This is crucial.

If the pickup photo is taken from a slightly different angle, the system could falsely say the whole door is damaged. To prevent this, OpenCV feature matching finds common points in both images and aligns them.

Techniques used:
- ORB feature matching
- RANSAC to estimate the transform
- homography to warp the image

This makes the pixel-level comparison meaningful.

### Step 5: Convert to LAB Color Space
LAB is chosen because it separates:
- lightness (L)
- green-red axis (A)
- blue-yellow axis (B)

This helps detect actual visual changes more reliably than plain RGB.

### Step 6: Difference Detection
The project compares the pickup image against the drop-off image using:
- luminance difference
- chroma difference
- edge change difference

This means there are multiple signals, not just one threshold.

### Step 7: Filter False Positives
A real scratch or dent is not the same as:
- lighting differences,
- panel reflections,
- shadows,
- camera noise,
- body seams,
- moving objects in background.

The code uses morphological operations and filtering to remove noise and retain only meaningful changed regions.

### Step 8: Find Connected Regions
After thresholding, the system finds contiguous changed areas using connected component analysis.

Each region becomes a candidate and gets a bounding box.

### Step 9: Score Each Candidate
Each region gets metrics like:
- area
- aspect ratio
- mean difference
- edge strength
- color shift
- confidence score

These scores tell the system whether the region is likely a real damage region or just noise.

### Step 10: Label the Damage Type
The program tries to label the region as:
- dent
- scratch
- rust-like damage
- paint difference

This is done using heuristics and optionally a trained model.

### Step 11: Generate Visual Proof
The system renders a comparison image and highlights the damaged region. This is then shown to the customer and manager.

This is the “why” behind OpenCV in the project: it gives objective visual evidence.

---

## 7. How the Damage Detection Module Fits into the Whole Project

The damage detection is not a separate isolated AI toy. It is part of the end-to-end custody pipeline.

The full flow is:

- car checked in
- photos captured
- specifics stored
- openCV compares photos
- findings generated
- customer sees the report
- manager verifies the result
- audit log records the decision

This makes the damage module a trust-building system rather than only an image classifier.

---

## 8. Geofence and Security Module

The project also tracks whether the vehicle moved unexpectedly.

### backend/ai/anomaly/geofence.py
This file contains the actual geofence math.

It uses:
- haversine distance formula
- bearing calculations
- point-in-polygon checks
- safe zone evaluation

It classifies movement as:
- NORMAL
- WARNING
- SUSPICIOUS
- BREACH

### backend/services/geofence.py
This service evaluates telemetry against the geofence and raises alerts when needed.

Important logic:
- if a vehicle leaves the approved area, generate alert
- if telemetry is stale or missing, flag risk
- if speed is unusual, raise an alert
- if the vehicle is moving unexpectedly while parked, log suspicious activity

### Why this is important
The platform is not just about vehicle condition. It is also about safety and unauthorized movement.

---

## 9. GPS and Telemetry Architecture

The project brings in GPS positions and checks whether they align with parking rules.

### backend/models/gps_event.py
Stores each GPS fix with data like:
- latitude
- longitude
- timestamp
- speed
- accuracy
- source

### backend/services/security.py / monitoring logic
Processes incoming telemetry and evaluates if the current position is safe or suspicious.

### frontend tracking pages
The frontend plots these events on a map and shows the current status of the car.

This supports the real business feature: the customer can see if the vehicle is moving outside the allowed zone.

---

## 10. Frontend Architecture

### frontend/src/main.jsx
This is the app bootstrap file.

It wraps the app with:
- BrowserRouter
- QueryProvider
- ToastProvider
- AuthProvider

This is the root provider structure.

### frontend/src/App.jsx
This simply renders the application route tree.

### frontend/src/routes/
This contains route configuration and role-based access rules.

### frontend/src/services/apiClient.js
This is the central API client.

It does:
- attach JWT token
- call backend endpoints
- normalize errors
- unwrap backend response envelope
- auto-logout on unauthorized errors

This is very important in real apps because all API calls go through one place.

### frontend/src/context/AuthContext.jsx
Handles user login status, token state, and auth information.

### frontend/src/pages/
Contains user pages such as:
- login
- dashboard
- vehicle detail
- condition report
- valet dashboard
- tracking page
- alerts page

### frontend/src/components/
Contains visual widgets and reusable UI parts:
- cards
- panels
- tracking map
- alert cards
- inspection comparison UI
- dispute modal

### Why this frontend structure matters
It keeps UI components reusable and logically organized. The project is large, so a good frontend structure is essential.

---

## 11. Security and Authentication Design

The project uses a serious authentication and authorization model.

### JWT Authentication
- user logs in
- server issues a JWT token
- frontend stores the token
- each API request includes the token

### Roles
Typical roles may include:
- customer
- valet operator
- valet manager
- admin

### Authorization
The backend checks the role before allowing actions like:
- verify damage
- resolve disputes
- change geofence
- access certain dashboards

This prevents unauthorized use of sensitive functions.

### Why it matters
Any system that handles vehicle custody and damage disputes must be secure. Otherwise anyone could fake an inspection or alter the timeline.

---

## 12. Database Design and Why It Matters

The database stores the core domain entities:
- users
- vehicles
- tickets
- custody sessions
- inspections
- GPS events
- parking zones
- alerts
- audit logs

This is a proper relational system instead of a loose JSON store.

The project is designed so that every important transition is connected:
- vehicle -> custody session -> ticket -> inspection -> alert -> audit log

This is what makes the platform credible in a real business scenario.

---

## 13. Why the Project Uses Separate Modules

The project is carefully modular. This is not accidental.

### Separation of concerns
Each module handles one area:
- routes = HTTP layer
- services = business logic
- models = database schema
- AI = analysis logic
- frontend = user interface

This helps in:
- maintenance
- testing
- debugging
- extension
- team collaboration

If everything were in one file, it would be hard to manage and impossible to explain in an interview.

---

## 14. Why OpenCV Was Chosen for Damage Detection

OpenCV is used because it is a strong computer vision library for:
- image alignment
- edge detection
- contour extraction
- morphological filtering
- image overlays
- object detection-like region extraction

In this project, the system does not need a huge deep learning pipeline. It needs a transparent, explainable mechanism that can compare before/after images and find regions of likely change.

This is why classical CV is preferred here:
- easier to explain
- easier to debug
- more transparent to management and customers
- less dependent on massive training data

The project still includes an ML classifier option, but the base solution is classical CV because that is reliable, auditable, and explainable.

---

## 15. What the System Actually Proves to the User

The platform is designed to prove the following:

- where the vehicle was,
- whether it moved outside the zone,
- whether there was a damage candidate,
- whether the damage was confirmed or rejected,
- who made the decision,
- what timestamps were involved,
- whether the vehicle was safely returned.

This is the foundation of a trust-based valet system.

---

## 16. Full End-to-End Example

Imagine a customer drops off a car.

1. The car is registered to the customer.
2. A valet ticket is created.
3. The drop-off images are uploaded.
4. The system stores the baseline state.
5. The customer later asks for the vehicle to be returned.
6. Pickup photos are uploaded.
7. The system compares them to the drop-off images.
8. OpenCV identifies changed regions.
9. The damage areas are scored and labelled.
10. The report shows the location and severity.
11. A manager verifies it.
12. The entire process is logged.

At every step, the system leaves a digital trail.

---

## 17. Key Technical Concepts You Should Know for an Interview

### 1. Why is Flask used?
Because Flask is lightweight, flexible, and ideal for creating a modular REST API. It fits the project’s architecture without unnecessary overhead.

### 2. Why is PostgreSQL used?
Because the project has relational relationships and audit data, and PostgreSQL handles that well with strong constraints and data integrity.

### 3. Why is React used instead of Streamlit or Gradio?
Because this project is a real web app with a proper authentication flow, role-based access, API communication, maps, and dashboards. React fits this better.

### 4. Why separate frontend and backend?
Because the project is production-like and follows real-world architecture. The frontend is a user interface, while the backend handles business logic and security.

### 5. Why use OpenCV for damage detection?
Because it offers reliable image comparison, edge detection, and region extraction in a transparent and explainable way.

### 6. What is the purpose of the geofence?
To define the legal or safe area in which the vehicle is allowed to move. Any motion outside that area is suspicious.

### 7. Why is the audit log important?
Because trust in a valet and vehicle security system depends on accountability. The system must prove what happened and by whom.

### 8. Why have role-based auth?
Because staff, managers, and customers do not all have the same permissions.

### 9. Why detect damage at pickup?
Because pickup is the point where the condition is compared against the drop-off baseline.

### 10. Why do we need verification?
Because AI/detection heuristics can be wrong. A manager verifies the result before it becomes a final record.

---

## 18. Likely Interview Questions and Strong Answers

### Q1: What problem is your project solving?
Answer: It solves the lack of transparency during valet custody. Customers cannot see whether their car moved, where it was, or whether it was damaged. The project creates a traceable, auditable custody record.

### Q2: What is the architecture of the project?
Answer: It follows a layered architecture with React frontend, Flask backend, PostgreSQL database, OpenCV AI layer, and geofence-based security checks. Each layer is separated and communicates through APIs.

### Q3: Why did you use OpenCV instead of only a neural network?
Answer: Because explainability, transparency, and low-data suitability matter here. The system needs to compare before/after photos, detect changed regions, and show visual evidence. OpenCV fits that directly.

### Q4: How does damage detection work?
Answer: The system aligns the drop-off and pickup photos, compares them in LAB color space, extracts difference regions, filters noise, finds connected regions, scores them, and labels them as likely damage. The output includes a confidence score and evidence image.

### Q5: What is a geofence?
Answer: A geofence is a defined safe zone. The system uses GPS coordinates to determine whether the vehicle stays inside or leaves the approved area.

### Q6: Why is audit logging important?
Answer: It makes the system trustworthy and legally reviewable. Every important action is recorded with metadata and person attribution.

### Q7: How is security handled?
Answer: Through JWT authentication, role-based access control, secure API endpoints, validation, and restricted actions for managers and admins.

### Q8: Why are there separate files for routes, services, and models?
Answer: To keep responsibilities clean. Routes handle HTTP, services handle business logic, and models handle data storage. This is good software design and improves testability.

### Q9: What is the biggest challenge in this project?
Answer: Handling false positives in damage detection and making sure the system does not misclassify lighting changes, reflections, or alignment errors as actual damage.

### Q10: How is the project different from a basic parking tracker?
Answer: This project includes custody flow, damage analysis, vehicle security, geofencing, audit logs, dispute management, and customer-visible dashboards. It is more than tracking; it is a full operational system.

---

## 19. Short Presentation-Friendly Summary

This project is an AI-powered valet transparency and vehicle security platform. It records a vehicle’s custody lifecycle, monitors the vehicle’s location, detects suspicious movement with geofencing, compares before and after vehicle images using OpenCV, identifies damage candidates, and records every action in an audit log. The platform combines React on the frontend, Flask and PostgreSQL on the backend, and OpenCV-based vision logic for damage analysis. It is built to improve trust, accountability, and operational visibility in valet parking and vehicle custody management.

---

## 20. Final Message for a 2nd Presentation

If I had to explain this project in one line: it turns a traditional valet process into a transparent, trackable, evidence-based system using web technology, AI, and security controls.

The core value is not just “tracking a car”; it is building trust through proof: proof of where the vehicle was, proof of what condition it was in, proof of what happened during custody, and proof of who approved or disputed the result.

That is the real business value of this project.
