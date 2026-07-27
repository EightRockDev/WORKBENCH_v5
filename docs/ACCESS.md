# Accessing the Eight Rock Workbench

Two ways to run it, depending on where you are in the rollout.

---

## 1. Local / pre-public testing (no login screen)

For validating on the server before real sign-in is wired. You launch it and are
auto-signed-in as an admin — no login prompt.

```powershell
cd C:\WORKBENCH_V5
$env:ER_DEV_LOGIN=1
uv run python -m streamlit run app.py
```

Open **http://localhost:8501**. The sidebar shows *"Signed in: Dev (local) ·
admin"* and a **Admin panel** toggle. This mode is for you on the box only; it
is NOT how outside users get in.

> First time with an empty inventory? Load demo data so there's something to
> click: `uv run python scripts/seed_demo_properties.py` (12 Hampton Roads
> properties; clearly-labeled demo rows, replaced by real data in Phase 0).

**Turn dev mode OFF before going public:** open a new PowerShell (so
`ER_DEV_LOGIN` is not set) or run `Remove-Item Env:\ER_DEV_LOGIN`.

---

## 2. Real sign-in (Google / Microsoft / email-password)

This is how you and pilot users log in once the app is public. It uses
Streamlit's built-in OpenID Connect with **Auth0** as the hosted login provider
(free up to 25,000 users; Microsoft Entra External ID is the alternative).

### One-time Auth0 setup (~10 minutes)

1. Create a free account at **auth0.com** and a new **Regular Web Application**.
2. In the app's **Settings**, note **Domain**, **Client ID**, **Client Secret**.
3. Set **Allowed Callback URLs** to your app's address plus `/oauth2callback`:
   - Public:  `https://workbench.eight-rock.com/oauth2callback`
   - (Local test, optional): `http://localhost:8501/oauth2callback`
4. Under **Authentication → Social**, enable **Google** (and **Microsoft** if you
   want it). Email/password ("Database" connection) is on by default.

### Wire it into the app

Copy the template and fill in the three Auth0 values:

```powershell
cd C:\WORKBENCH_V5
copy .streamlit\secrets.toml.example .streamlit\secrets.toml
notepad .streamlit\secrets.toml
```

In `[auth]` set a strong random `cookie_secret`
(`python -c "import secrets;print(secrets.token_urlsafe(48))"`), your
`redirect_uri`, and under `[auth.auth0]` the `client_id`, `client_secret`, and
`server_metadata_url`
(`https://YOUR_TENANT.us.auth0.com/.well-known/openid-configuration`).
Also set the `[postgres] url` to the `DATABASE_URL` line from your `.env`.

Once `[auth]` is present, the app **automatically** shows a real **Log in**
button instead of the dev bypass.

### How login works for everyone

- Users click **Log in** and authenticate with Google, Microsoft, or
  email/password.
- **The very first person to sign in becomes the admin** (you — sign in first).
- **Everyone after lands on a "pending approval" screen** and sees no data until
  you approve them. Approve people in the **Admin panel**: flip the *Admin panel*
  toggle → **Approve** next to their name, and set their role.
- Roles/permissions come from the 18-preset library (Principal, Analyst,
  Investor/LP, Broker guest, etc.).

---

## Making it reachable on the internet

Running locally serves only `http://localhost:8501` on the box itself. To let
users reach it from anywhere:

1. Run `deploy\windows\install.ps1` (installs Caddy + registers the app as a
   Windows service on `127.0.0.1:8501`, with Caddy terminating public HTTPS).
2. Point your domain's **DNS A-record** (`workbench.eight-rock.com`) at the
   server's reserved public IP.
3. **Port-forward** public 80 + 443 on your router to the box.

Caddy fetches a free HTTPS certificate automatically, and users reach
`https://workbench.eight-rock.com`. See `docs/SETUP.md` and
`deploy/windows/README.md` for the full runbook.

## Office-network access (Windows service) — LIVE

One-time, on the server: double-click **`install-service.bat`** (it asks for
admin rights and a **passcode** of your choosing). From then on:

- The workbench **starts itself with Windows** — no console window, survives
  reboots and log-offs. Logs land in `logs\service-*.log`.
- Open it from **any device on the office network** at
  `http://<server-ip>:8501` (the installer prints the exact address).
- Every visitor must enter the **passcode** first (`ER_APP_PASSCODE` in
  `.env`). This is the interim gate until Auth0/Entra sign-in is wired; the
  firewall rule is private-network-only, so nothing is exposed to the
  internet.
- To go back to the double-click console workflow: `uninstall-service.bat`.
