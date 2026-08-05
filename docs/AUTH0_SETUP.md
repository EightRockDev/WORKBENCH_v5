# Turning on real login (Google / Microsoft / email + password)

A detailed, click-by-click walkthrough for standing up sign-in on
`https://workbench.eight-rock.com`. Written for Brian — no prior Auth0
experience assumed. Budget ~20–30 minutes.

**What we're building:** one hosted login screen (run by Auth0) that offers
"Continue with Google," "Continue with Microsoft," and email + password. When
someone signs in, the workbench receives their verified email/name and matches
it to a user record. **The very first person to log in becomes the admin
(you); everyone after lands on a "pending approval" screen until you approve
them** from the Admin panel.

The app code is already done. You only need to (A) set up Auth0, (B) drop one
config file on the server, (C) make sure Postgres is running, and (D) restart.

---

## Before you start — two facts

- **Google + email/password can be live tonight** with almost no extra work
  (Auth0 supplies test keys for Google out of the box).
- **Microsoft ("Continue with Microsoft") needs a 5-minute Azure app
  registration** to get a Microsoft client ID/secret. It's included below as
  Part B-3; if you want to go live tonight with just Google + email and add
  Microsoft tomorrow, that's totally fine — skip B-3 for now.

---

## Part A — Create the Auth0 application

1. Go to **https://auth0.com** → **Sign up** (free). Pick the **US** region when
   asked (matches the `.us.auth0.com` domain in our template).
2. After signup you land in the Auth0 **Dashboard**. On the left, click
   **Applications → Applications**.
3. Click **+ Create Application**.
   - **Name:** `Eight Rock Workbench`
   - **Type:** choose **Regular Web Applications** (NOT SPA, NOT Machine-to-
     Machine). Click **Create**.
4. On the next screen it may ask "What technology are you using?" — click
   **Skip integration** (we're wiring it by hand, not with their sample).
5. You're now on the application's page. Click the **Settings** tab. Keep this
   tab open — you'll copy three values from it in Part D and paste two URLs in
   Part C.

---

## Part C — Set the callback + logout URLs

(Still on the application's **Settings** tab.) Scroll to **Application URIs**.

1. **Allowed Callback URLs** — paste BOTH of these, comma-separated:
   ```
   https://workbench.eight-rock.com/oauth2callback, http://localhost:8501/oauth2callback
   ```
   (The `localhost` one lets you test on the server itself; the public one is
   what outside users use. The `/oauth2callback` path is Streamlit's — don't
   change it.)
2. **Allowed Logout URLs** — paste:
   ```
   https://workbench.eight-rock.com, http://localhost:8501
   ```
3. Scroll to the bottom and click **Save Changes**. (Leave this tab open.)

> ⚠️ The #1 cause of a failed login is a callback-URL typo. It must match
> `redirect_uri` in the config file (Part E) **exactly** — scheme, host, and
> `/oauth2callback` path.

---

## Part B — Turn on the three sign-in methods

### B-1. Email + password (already on)
Auth0 calls this the **Username-Password-Authentication** database connection,
and it's enabled by default. To confirm: left nav **Authentication →
Database** → you should see `Username-Password-Authentication`. Click it →
**Applications** tab → make sure **Eight Rock Workbench** is toggled **on**.

### B-2. Google (test keys — instant)
1. Left nav **Authentication → Social**.
2. If **google-oauth2** isn't listed, click **+ Create Connection → Google /
   Gmail → Continue → Create** (you can leave Client ID/Secret blank to use
   Auth0's development keys for now).
3. Click the **Google** connection → **Applications** tab → toggle
   **Eight Rock Workbench** **on** → **Save**.

> Auth0's dev keys are fine for testing and small use. For heavy production use
> later, add your own Google OAuth client (Google Cloud Console → Credentials →
> OAuth client ID → Web app; authorized redirect URI
> `https://YOUR_TENANT.us.auth0.com/login/callback`) and paste its ID/secret
> into this connection. Not required tonight.

### B-3. Microsoft (needs an Azure app registration — optional tonight)
1. In a new tab go to **https://portal.azure.com** → **Microsoft Entra ID** →
   **App registrations → + New registration**.
   - **Name:** `Eight Rock Workbench`
   - **Supported account types:** "Accounts in any organizational directory and
     personal Microsoft accounts" (so both work/school and personal Microsoft
     logins work).
   - **Redirect URI:** platform **Web**, value
     `https://YOUR_TENANT.us.auth0.com/login/callback`
     (replace `YOUR_TENANT` with your Auth0 domain prefix — you'll know it after
     Part D). Click **Register**.
2. On the app's **Overview**, copy the **Application (client) ID**.
3. Left nav **Certificates & secrets → + New client secret** → copy the
   secret **Value** (not the ID) immediately — it's shown once.
4. Back in **Auth0 → Authentication → Social → + Create Connection → Microsoft
   Account**. Paste the Azure **client ID** and **secret**. Click **Create**.
5. Open the **Microsoft** connection → **Applications** tab → toggle
   **Eight Rock Workbench** **on** → **Save**.

---

## Part D — Copy the three Auth0 values

Back on **Applications → Eight Rock Workbench → Settings**, copy these (top of
the page):

- **Domain** — looks like `eightrock.us.auth0.com`
- **Client ID** — a long string
- **Client Secret** — click the eye/copy icon to reveal

You can send me the **Domain** and **Client ID** in chat (they're not secret).
**Do NOT paste the Client Secret in chat** — it goes only in the file in Part E.

---

## Part E — Put the config on the server

On the workbench PC:

```powershell
cd C:\WORKBENCH_V5
copy .streamlit\secrets.toml.example .streamlit\secrets.toml
python -c "import secrets; print(secrets.token_urlsafe(48))"   # copy the output
notepad .streamlit\secrets.toml
```

Make the file look like this. **All five auth keys go directly under `[auth]`** —
do NOT nest them under `[auth.auth0]`. The app calls `st.login()` with no
provider name, so it reads the single default provider; a nested block makes it
report "missing keys: client_id, client_secret, server_metadata_url" even though
they're present.

```toml
[auth]
redirect_uri        = "https://workbench.eight-rock.com/oauth2callback"
cookie_secret       = "PASTE_THE_TOKEN_YOU_GENERATED"
client_id           = "PASTE_CLIENT_ID_FROM_PART_D"
client_secret       = "PASTE_CLIENT_SECRET_FROM_PART_D"
server_metadata_url = "https://YOUR_DOMAIN/.well-known/openid-configuration"
```

- Replace `YOUR_DOMAIN` with the **Domain** from Part D, e.g.
  `https://eightrock.us.auth0.com/.well-known/openid-configuration`.
- **No `[postgres]` block** when you set up the database with `setup-db.ps1`
  (Part F) — that script writes the real `DATABASE_URL` into `.env`, and
  `secrets.toml`'s `[postgres].url` would *override* it. Only add a `[postgres]`
  block here if you are deliberately not using `.env` for the database.
- Generate `cookie_secret` with either:
  - PowerShell (no Python needed): `$b=New-Object 'byte[]' 48; [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b); [Convert]::ToBase64String($b).TrimEnd('=').Replace('+','-').Replace('/','_')`
  - or Python: `python -c "import secrets; print(secrets.token_urlsafe(48))"`
- Save as `secrets.toml` (NOT `secrets.toml.txt` — turn on Explorer's "File name
  extensions" so a stray `.txt` can't hide). This file is gitignored.

---

## Part F — Make sure Postgres is running

Real login **requires** Postgres (it stores the user records). If Postgres
isn't up, the app quietly falls back to no-login mode and the sign-in screen
never appears.

- Confirm the `[postgres] url` above points at your live database.
- The app applies the required tables automatically on start (you don't run any
  SQL by hand).
- If you don't have Postgres installed yet, tell me — that's the one piece to
  stand up first, and I'll walk you through it.

---

## Part G — Restart and go live

```powershell
cd C:\WORKBENCH_V5
```
1. Right-click **`update-workbench.bat`** → **Run as administrator**. This pulls
   the latest code, installs the `authlib` dependency login needs, and restarts
   both app services.
2. In `.env`, **remove or comment out `ER_APP_PASSCODE`** (and make sure
   `ER_DEV_LOGIN` is not set) so the old shared passcode steps aside for real
   login. Then restart once more (rerun the updater or
   `Restart-Service WorkbenchBlue,WorkbenchGreen -Force` in an admin PowerShell).

---

## Part H — First login and approving others

1. Open `https://workbench.eight-rock.com`. You should see a **Log in** button →
   it takes you to the Auth0 screen with Google / Microsoft / email + password.
2. **Log in first, as yourself** — the first account becomes the **admin**.
3. When teammates log in after you, they'll see a **"pending approval"** screen.
   You approve them: open the **🔧 Admin** toggle (top-right) → the user list →
   **Approve**. You can also set roles and suspend accounts there.

> **Testing from inside the office network?** After you authenticate, the
> browser is redirected to `https://workbench.eight-rock.com/oauth2callback`.
> Many routers won't loop a request to your own public address back to the LAN
> (NAT hairpin), so that callback can time out *only from inside* — outside
> users are fine. Fix it for the office PC by pointing that hostname at itself:
> in an **elevated** PowerShell,
> `Add-Content C:\Windows\System32\drivers\etc\hosts "127.0.0.1`tworkbench.eight-rock.com"; ipconfig /flushdns`
> then browse the public URL directly. Caddy still serves the real cert (it's
> keyed to the hostname, not the IP), and external access is unaffected.

> **Not the first login, stuck on "pending approval"?** If a test account
> already claimed the admin slot, promote yourself directly (elevated
> PowerShell, reads the DB URL from `.env`):
> ```powershell
> $dburl = ((Select-String -Path C:\WORKBENCH_V5\.env -Pattern '^DATABASE_URL=').Line -replace '^DATABASE_URL=','')
> & 'C:\Program Files\PostgreSQL\16\bin\psql.exe' $dburl
> ```
> then at the `=#` prompt:
> ```sql
> UPDATE users SET platform_role='admin', status='active' WHERE email='YOUR_EMAIL';
> ```
> Expect `UPDATE 1`, `\q` to quit, then refresh the browser (no re-login needed —
> an existing user's role/status is never overwritten on login).

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| No "Log in" button, app opens straight to properties | `secrets.toml` missing the `[auth]` block, or Postgres not running (Part F). |
| Clicking Log in throws `StreamlitAuthError: ... missing keys ['client_id','client_secret','server_metadata_url']` | The auth keys are nested under `[auth.auth0]` (or another sub-table). Move all five directly under `[auth]` — `st.login()` reads the single default provider. Restart after editing (the file-watcher is off, so secrets reload only on restart). |
| App still behaves as before after editing `secrets.toml` | Secrets reload only on **restart** (`Restart-Service WorkbenchBlue,WorkbenchGreen -Force`) — the file-watcher is disabled. |
| Saved `secrets.toml` but the app ignores it | Windows may have saved it as `secrets.toml.txt` with the extension hidden. Turn on Explorer → View → **File name extensions** and rename to exactly `secrets.toml`. |
| "Callback URL mismatch" on the Auth0 screen | The Auth0 **Allowed Callback URLs** (Part C) must exactly equal `redirect_uri` (Part E), incl. `/oauth2callback`. |
| Callback **times out** but only from inside the office | NAT hairpin — see the "Testing from inside the office network?" note above (add a `hosts` entry). |
| Clicking Log in throws a Python error | `authlib` not installed — run the admin updater (Part G-1). |
| Google works but Microsoft is missing | Finish Part B-3 (Azure app registration + enable the Microsoft connection for this app). |
| Signed in but stuck on "pending approval" | Expected for everyone after the first user; approve them in Admin (Part H-3). If YOU are stuck, you weren't the first login — promote yourself with the SQL in the note above. |

Send me your **Domain** and **Client ID** and I'll sanity-check the
`server_metadata_url` before you restart.
