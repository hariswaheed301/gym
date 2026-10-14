# Gym Management System (Flask + PostgreSQL)

Server-rendered Flask app for a gym owner and reception staff: members, memberships, payments/dues,
**attendance (check-in / check-out)**, equipment permissions, dashboard, audit log.
No React/Node build. Bootstrap 5 + Bootstrap Icons are loaded from the jsDelivr CDN (allowed in the CSP);
for an offline/production install, vendor them into `app/static/` and tighten the CSP in `app/__init__.py`.

The setup below targets local development on Windows with Flask running in a Python virtual environment and
PostgreSQL running in Docker. The repository's Docker image uses Python 3.12. The application and full test suite
have been run successfully in the project's Python 3.14.6 Windows virtual environment; physical ZKTeco hardware
compatibility remains unverified until tested against the exact model and firmware.

## Windows Quick Start

### 1. Create the Python environment

Install Python 3.14 and Docker Desktop, then open PowerShell in the project directory:

```powershell
py -3.14 --version
py -3.14 -m venv .venv
.\.venv\Scripts\Activate.ps1
python --version
python -m pip install --upgrade pip
pip install -r requirements-dev.txt
```

If PowerShell blocks activation, run `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` in that terminal
and activate again. Keep `(.venv)` active for the commands below.

### 2. Create local settings

Create `.env` from the checked-in template only if `.env` does not already exist:

```powershell
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
py -3.14 -c "import secrets; print(secrets.token_hex(32))"
```

Put the generated value in `.env` as `SECRET_KEY`. Set one local database password consistently in `DATABASE_URL`,
`TEST_DATABASE_URL`, and `POSTGRES_PASSWORD`. With this repository's Docker mapping, the URLs use host port `5433`:

```dotenv
APP_ENV=development
SECRET_KEY=<generated-random-value>
DATABASE_URL=postgresql+psycopg://gym:<local-password>@127.0.0.1:5433/gym
TEST_DATABASE_URL=postgresql+psycopg://gym:<local-password>@127.0.0.1:5433/gym_test
POSTGRES_PASSWORD=<same-local-password>
```

Use a URL-safe local password or URL-encode special characters in the database URLs. `.env` contains secrets and is
ignored by Git; never commit or share it. The full list of optional settings is in `.env.example`.

For hardware device credentials, also generate a separate Fernet key and set `DEVICE_CREDENTIAL_KEY` in `.env`:

```powershell
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Keep this key private and backed up. If it is lost, saved device communication codes cannot be decrypted. Simulator
devices do not require it.

### 3. Start PostgreSQL and create the schema

Start Docker Desktop, then from the project directory:

```powershell
docker compose up -d db
docker compose ps
python -m flask db upgrade
```

Wait for the `db` service to report `healthy`. Compose runs PostgreSQL 16 and maps `localhost:5433` on Windows to
port `5432` inside the container. The `pgdata` volume persists database records across stops. The init SQL creates
`gym_test` when the volume is first initialized.

### 4. Add demo data and an admin

```powershell
python -m flask seed-demo
python -m flask create-admin
```

`seed-demo` adds example plans, equipment, and two members; it does not create memberships, payments, or attendance.
It adds its example equipment only when the equipment table is empty. To check database connectivity, use
`python -m flask db-health`. To add records manually, sign in as admin and use Members, Memberships, Payments, and
Equipment in the app. A member needs a current membership and (with the default policy) a partial payment to pass
check-in eligibility.

Run `create-admin` once and keep the username and password you enter. There are no default website credentials.
For the optional ZKTeco LAN adapter, install `pip install -r requirements-device.txt` in the active `.venv`.

### 5. Run and stop the app

```powershell
python -m flask run --debug
```

Open <http://127.0.0.1:5000>. Sign in with the admin account created above. Leave the server terminal running.
To stop Flask, press `Ctrl+C`. To stop PostgreSQL but keep its data, run `docker compose stop`; start it later with
`docker compose up -d db`. Do not run `docker compose down -v` unless you intend to delete the database volume.

### Share a local demo on the gym Wi-Fi

For a short demo only, run Flask on the laptop's LAN interface:

```powershell
python -m flask run --host 0.0.0.0 --port 5000
```

Find the laptop's private IPv4 address with `ipconfig`, allow TCP port 5000 only on the Windows **Private** network
profile, then open `http://<laptop-private-ip>:5000` from a device on the same Wi-Fi. The laptop must remain on and
connected. Flask's development server is not for production. An organization deployment should use a production
WSGI server behind TLS and a firewall, on a host routed to the device LAN. Do not port-forward the development app
or biometric terminal ports to the public internet.

## Docker: What It Runs

Docker is used here to run PostgreSQL without installing PostgreSQL directly on Windows. `docker-compose.yml`
defines the database, port mapping, persistent volume, health check, and a SQL initialization script. The Flask app
in the quick start runs locally from `.venv`; Docker does not replace Python or Flask. `Dockerfile` builds an
optional app container using Python 3.12. To run both services in Docker instead, use
`docker compose --profile app up --build`; the app container is available at <http://127.0.0.1:8000>.

`migrations/env.py` connects Alembic to the Flask app and database. `migrations/alembic.ini` points to the
migrations folder and configures logging. `migrations/versions/0001_initial.py` defines the initial schema.
`script.py.mako` is a template Alembic uses to generate future migration files; it is not a startup script.

## Configuration Reference

| Setting | Purpose |
|---|---|
| `APP_ENV` | Selects development, production, or testing configuration. |
| `SECRET_KEY` | Signs session and CSRF data; use a private random value. |
| `DATABASE_URL` | Main PostgreSQL connection used by local Flask. |
| `TEST_DATABASE_URL` | Separate PostgreSQL database used by pytest; name must end in `_test`. |
| `POSTGRES_PASSWORD` | Password Compose uses when initializing PostgreSQL. Existing database volumes retain the database's initialized password. |
| `GYM_NAME`, `GYM_TIMEZONE`, `DEFAULT_CURRENCY` | Gym name shown in the app, local display timezone, and currency. |
| `DUES_POLICY` | Whether unpaid/partially paid membership dues prevent member entry. |
| `LONG_SESSION_HOURS` | Threshold after which an open visit is flagged for review. |
| `SESSION_IDLE_MINUTES` | Session idle timeout. |
| `DEVICE_CREDENTIAL_KEY` | Fernet key used to encrypt saved hardware communication codes. Generate it with the command above; simulator devices do not require it. |
| `DEVICE_SYNC_MAX_RECORDS` | Maximum punches accepted in one manual hardware sync (default 5000); larger histories are rejected, not truncated. |
| `FORCE_HTTPS`, `TRUSTED_PROXY_COUNT` | Production HTTPS/proxy settings; leave at development defaults for local use. |

## Tests and Checks

Run these from the active `.venv` in PowerShell:

```powershell
python -m pytest
ruff check .
ruff format --check .
python -m flask db check
pip-audit -r requirements.txt
```

Tests use `TEST_DATABASE_URL` and refuse to run unless its database name ends in `_test`. The fixture clears test
tables, so never point it at the demo or production database. The current test suite has passed on Python 3.14.6.

## Roles

| Capability | Admin | Receptionist |
|---|---|---|
| Register/edit members, create/renew memberships, record payments, check-in/out, view history & reports | yes | yes |
| Grant discounts, see/edit internal notes | yes | no |
| Plans, deactivate members, suspend/cancel memberships, void payments | yes | no |
| Attendance corrections, equipment permissions, equipment inventory, biometric device configuration, staff, audit log | yes | no |

All checks are enforced server-side (`roles_required` decorators); hidden buttons are cosmetic only.

## Key business rules

**Access policy (`DUES_POLICY`)** – default `require_partial`:
`allow` = unpaid dues never block entry; `require_partial` = block only if nothing has been paid;
`require_full` = any outstanding balance blocks. Independent of the policy, entry is denied for inactive members,
no/expired/not-yet-started/suspended memberships, and outside a plan's access hours.

**Memberships** – `expiry = start + duration_days − 1` (inclusive). Status (`active/suspended/cancelled`) is
administrative; "current/expired/upcoming" is derived from dates; payment state (unpaid/partial/paid) is derived
from payments. Non-cancelled memberships of one member cannot overlap (service check **and** a PostgreSQL
exclusion constraint). **Renewal** starts the day after the latest membership expires (or today if already lapsed).

**Payments** – `NUMERIC(12,2)`/`Decimal` only. Manual recording (no gateway). Overpayment is rejected; the
membership row is locked during the write; a hidden idempotency key makes double submits harmless.
Payments are never deleted: **void** (admin, reason required) then record a new payment to correct one.

### Attendance and Visit Time

Reception staff search by member name, number, or phone, then use **Check-in / out**. The routes are in
`app/blueprints/attendance.py`; the business rules and timestamps are in `app/services/attendance.py`. A check-in
is accepted only if the member is active, has an eligible current membership, meets the configured dues policy, and
is within any plan access hours. The server records the timestamp in UTC; the UI displays it in `GYM_TIMEZONE`.

A visit remains open while `check_out_at IS NULL`. A partial unique index and row lock prevent two open visits for
one member. When staff check the member out, duration is `check_out_at - check_in_at`. The member detail page shows
the total duration across completed visits plus the currently open visit as of page load; refresh the page to update
that total. Attendance history shows each visit's duration. On the reception page, the inside list refreshes every
30 seconds and the browser updates elapsed visit timers every minute.

### Missed Check-out and No-check-in Logic

The app does not automatically check anyone out. Open visits exceeding `LONG_SESSION_HOURS` appear as possible
missed check-outs. An administrator reviews the actual time and uses **Correct**, entering a reason; the correction
is marked and the old and new values are recorded in the audit log. Do not assume a person left at closing time.

The daily report's **No check-in recorded** list is derived for the selected gym-local date. It includes active
members whose membership status and dates make it current that day, and who have no check-in record for that day.
This is not a confirmed absence: the app has no class schedule, required visit frequency, or attendance cutoff time.
It deliberately does not write permanent absence records. A real absence feature needs schedules/expected attendance
and a cutoff rule. Visits crossing midnight belong to their gym-local check-in date; open visits are excluded from
the completed-visit average.

### Biometric Device Setup (LAN)

Sign in as admin and open **Attendance devices**. Add a **Simulator** to demonstrate member mapping and punches
without hardware. For a physical terminal, install `requirements-device.txt`, choose **ZKTeco TCP/UDP**, and
configure a reserved private LAN IP, model-confirmed port/transport, timeout, and the terminal time zone. The common
local protocol often uses port `4370`, but verify the exact model and firmware. This is a best-effort adapter using
an unofficial library, not a claim of universal ZKTeco compatibility. Use **Test connection** before syncing.

Map each terminal user ID to an existing gym member. The terminal ID is never assumed to equal the gym member ID.
Configure the exact event field (`punch` or `status`) and disjoint IN/OUT codes from verified device records.
Unknown or ambiguous directions are recorded but do not create visits. **Sync now** fetches events manually. The
import stores event metadata, deduplicates by device/event key, converts device-local timestamps to UTC, and uses
the existing membership/dues eligibility rules for check-in. It does not enroll users, download fingerprint/face
templates, or clear terminal logs.

Each device displays online/offline state and its last connection error. If a device is offline, prior app data is
kept; retry after restoring LAN connectivity. This version has no background polling process or vendor ADMS/cloud
push connector. The application server must be routed to the terminal over the organization LAN. A public cloud
host cannot normally connect to a private LAN IP. Keep public demos on Simulator mode with fake members; never
port-forward a biometric terminal to the internet.

Before enabling a real device, verify its exact model and firmware, sample event fields, time zone, direction codes,
LAN reachability, and member-ID mappings. Communication keys are encrypted using `DEVICE_CREDENTIAL_KEY`; back up
that secret securely because losing it makes saved keys unreadable. Attendance “absence” remains a no-check-in
indicator until the gym defines expected attendance schedules and a cutoff time.

**Equipment permission precedence** – member *deny* > member *allow* > unrestricted equipment > plan grant >
denied. An explicit restriction can never be overridden by a plan. Usage records are **manually reported**
by staff; the system cannot detect physical use.

## Layout

```
app/  __init__.py (factory, CSP, errors)  config.py  models.py  forms.py  security.py  utils.py  cli.py
      blueprints/ auth dashboard members memberships payments attendance devices equipment staff reports audit
      services/   access attendance devices memberships payments equipment members reports audit
      templates/  devices/ attendance/ members/ memberships/ payments/ reports/ staff/ equipment/ ...
migrations/ (Alembic, 0001_initial, 0002_attendance_devices)  tests/ (pytest)
docker-compose.yml  Dockerfile  .gitignore  .dockerignore
```
Business logic lives in `app/services/` and takes plain ids, so a future REST API (`/api/v1`) or mobile app can reuse it.
JSON/partial endpoints used now: `GET /attendance/?q=` and `GET /attendance/inside` (HTML fragments for live search
and the 30-second list refresh; timers update every minute in the browser with no DB writes).

## Security notes and honest limitations

Implemented: Argon2id hashing, CSRF (Flask-WTF), session cookies HttpOnly/SameSite=Lax (+Secure in production),
sliding idle timeout, session invalidation on password reset/deactivation, login rate limiting, generic login errors,
open-redirect protection, CSP + security headers (Talisman), parameterised SQL, autoescaped templates, audit log for
logins, payments, voids, corrections, permission and staff changes, encrypted device communication codes, and
deduplicated device punch history. Device pages and sync actions are admin-only.

Limitations: biometric adapter is an unofficial, model-dependent pyzk TCP/UDP integration and has not been tested
against physical hardware; manual sync only (no background poller or vendor push protocol); the local Flask server
is for development/demo only; not penetration-tested; rate-limit storage is per-process `memory://` (use Redis/another
`RATELIMIT_STORAGE_URI` with several workers); CDN assets lack SRI hashes; no profile-image upload (deliberately
omitted rather than half-implemented); no password-change self-service page or MFA; single gym (add a `branch_id`
later); dashboard sums assume one currency; no data-retention job (members/payments are deactivated, never deleted —
define a policy before storing real personal data); the app role should be a non-superuser with DML rights only
in production (migrations need rights to create tables/extensions).
Production: set `APP_ENV=production`, a 32+ char `SECRET_KEY`, `FORCE_HTTPS=true` behind TLS, and
`TRUSTED_PROXY_COUNT` to the number of proxies in front.

## Git Preparation

`.gitignore` excludes `.env` (including local `.env.*` files except the safe `.env.example`), virtual environments,
local databases, logs, backups, caches, and build output. `.dockerignore` keeps the same private/runtime files out of
container build context. Keep migrations, source files, tests, `requirements*.txt`, and `.env.example` in version
control. Before staging, inspect `git status --short` and confirm `.env` is not listed. Do not upload device keys,
database dumps, real member data, or biometric templates.

This extracted folder has no Git history/remote yet. For an existing GitHub project, clone that repository first,
create a feature branch from its `main`, then copy only the project source/config files into that checkout; do not
initialize a second unrelated Git history inside the extracted folder. For a brand-new repository, create an empty
GitHub repository, install Git for Windows, and follow GitHub's instructions. Review staged filenames before every
commit. Never force-push or commit `.env`.

## Backup and restore

```bash
docker compose exec -T db pg_dump -U gym -Fc gym > backups/gym-$(date +%F).dump      # backup
docker compose exec -T db pg_restore -U gym -d gym --clean --if-exists < backups/gym-YYYY-MM-DD.dump   # restore
```
Schedule daily dumps, copy them off-machine, keep them encrypted (they contain personal data), and test a restore.

## Next steps
Run `pytest`, fix any first-run issues, run `flask db check`, create staff accounts, vendor Bootstrap, add SRI/Redis
rate limiting, then consider a self-service password page, QR/RFID check-in (`check_in_method` already exists), a
`/api/v1` layer over `app/services`, and branch support.
