# Connecting your Outlook mailbox (Module D)

## The privacy model — read this first

**Private mailbox, shared pipeline.**

- The mailbox you connect belongs to **you alone**. Your raw messages are
  readable only by your own login — **not by your colleagues, and not by an org
  admin**. This is enforced by PostgreSQL row-level security (the database
  itself refuses to return another user's rows), not by hiding buttons.
- The **deals, term sheets and contacts** extracted from that mail **are**
  shared with your organization. That's the point of the module: the pipeline is
  team work product, the correspondence is personal.
- **Disconnecting deletes your stored messages.** The deals you already created
  stay, because they belong to the org.
- Access tokens are **encrypted at rest** (Fernet/AES) with a key that lives only
  in `.env`. The database never stores a plaintext credential.
- The app requests **`Mail.Read` only** — it can read, never send.

---

## One-time setup (two values in `.env`)

**The short version:** get the client ID from step 2 below, then double-click
**`setup-inbox.bat`** in `C:\WORKBENCH_V5`. It generates the encryption key for
you and writes both values into `.env`. The only thing you type is the client ID.

### 1. A token-encryption key

`setup-inbox.bat` creates this (`ER_TOKEN_KEY`) automatically and will **never
overwrite an existing one** — regenerating it would disconnect every mailbox
already connected. To do it by hand instead, on the server in `C:\WORKBENCH_V5`:

```powershell
uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Copy the output and add it to `.env`:

```
ER_TOKEN_KEY=<the key you just generated>
```

> Keep this key. If you lose it, connected mailboxes must be reconnected
> (tokens become undecryptable — which is the point).

### 2. A Microsoft Entra app registration

You need a client ID so Microsoft knows which app is asking. Free, ~3 minutes:

1. Go to <https://entra.microsoft.com> → **Applications** → **App registrations**
   → **New registration**.
2. Name it `Eight Rock Workbench`. For "Supported account types" pick
   **Accounts in any organizational directory and personal Microsoft accounts**
   (or single-tenant if you only ever connect company mailboxes).
3. Leave the Redirect URI **blank** and click **Register**.
4. On the **Overview** page copy the **Application (client) ID**.
5. Go to **Authentication** → scroll to **Advanced settings** → set
   **Allow public client flows** = **Yes** → **Save**.
   *(This enables the device-code flow, which is what lets you sign in without a
   public HTTPS redirect URL.)*
6. Go to **API permissions** → **Add a permission** → **Microsoft Graph** →
   **Delegated permissions** → check **`Mail.Read`** and **`User.Read`** → **Add**.

Now double-click **`setup-inbox.bat`** and paste the client ID when it asks. It
writes `MS_GRAPH_CLIENT_ID` (and the encryption key) into `.env` for you. By
hand, the line is:

```
MS_GRAPH_CLIENT_ID=<the Application (client) ID>
```

Restart the app (`start-workbench.bat`).

---

## Connecting (each user does this themselves)

1. Open **CRM & Sourcing → 📥 Inbox → Deal**.
2. Expand **🔗 Connect my Outlook mailbox** → **Start sign-in**.
3. The app shows a short code and a link (`microsoft.com/devicelogin`).
   Open it on any device, enter the code, and approve.
4. Back in the app, click **I approved it — finish connecting**.
5. Click **🔄 Sync inbox**.

Your own mail is now ingested. Each teammate repeats these steps for their own
mailbox; nobody sees anyone else's.

---

## Notes

- **No mailbox connected?** The module runs on demo fixtures so you can try the
  workflow before wiring anything up.
- **Gmail** is supported by the same abstraction (`ER_INBOX_PROVIDER=gmail` +
  `GMAIL_TOKEN`), though the device-code convenience flow is Outlook-only today.
- Ingest is **idempotent per user**: pressing Sync repeatedly will not duplicate
  messages, deals, or term sheets.
- **"AADSTS54005 — code already redeemed"** means that one-time sign-in code was
  already used (or the page was refreshed after finishing). Each code works
  once. Click **Get a new code** and run through the three steps again.
