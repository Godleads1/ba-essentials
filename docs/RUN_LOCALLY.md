# Run B & A Essentials on your computer (Windows)

A step-by-step guide for running the app on your own computer to try it out,
written for Windows PowerShell. Everything here uses made-up demo data. It
never touches the live site.

## The one rule: two windows

Open **two** PowerShell windows, both in the project folder
(`C:\Users\HomePC\Documents\banda-app`):

| Window | What it's for | What you type there |
| --- | --- | --- |
| **1. Server** | Runs the app and shows sign-in codes | Only `runserver`. Then leave it alone. |
| **2. Commands** | Everything else | `git pull`, `migrate`, `seed_demo`, shell commands |

If the browser says **"127.0.0.1 refused to connect"**, window 1 has stopped.
Start the server again there.

To get into the folder, type:

```powershell
cd C:\Users\HomePC\Documents\banda-app
```

Always type `.\` in front of `.venv`: PowerShell needs it to run a program in
the current folder. Copy only what's inside a grey box, never the words
around it.

## First time only: set it up

In window 2:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --require-hashes -r requirements\dev.txt
Copy-Item .env.example .env
mkdir var
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py seed_plans
.\.venv\Scripts\python.exe manage.py seed_demo --yes
```

`seed_demo` makes a demo business with requests, files, messages and support
requests, and prints the demo accounts and one password for the staff demo
accounts. It only runs on a local copy (it refuses unless `DEBUG` is on). When
it asks you, add `--yes` to the **same line**. Never run it against the live
site.

To load the real service catalogue (all the tasks from the task bank):

```powershell
.\.venv\Scripts\python.exe manage.py bootstrap_catalogue catalogue/service_catalogue_v2.1.json
```

## Every time: start the server

In window 1:

```powershell
cd C:\Users\HomePC\Documents\banda-app
.\.venv\Scripts\python.exe manage.py runserver
```

Wait for `Starting development server at http://127.0.0.1:8000/`, then open
<http://127.0.0.1:8000/> in your browser.

## Sign in as a client

Clients sign in with a one-time code, not a password.

1. Go to <http://127.0.0.1:8000/login/>. Use a private window if you're
   already signed in as staff.
2. Enter `demo.owner@example.test` and press Continue.
3. Look in **window 1**. On your computer the app doesn't send real emails; it
   prints them there. Find the newest line that says
   `Your code to sign in is: 123456`.
4. Type that code. Each code works once, for 5 minutes, and only the newest
   one works.

**No code appears in window 1?** Then no code was sent. For security the page
looks the same either way, but the app sends no code when:

- the email is a **staff** account (staff never get codes; see below)
- there's **no account** with that email (run `seed_demo` first)
- too many codes were asked for in the last hour (stop the server with Ctrl+C
  in window 1 and start it again)

## Sign in as staff (operations, reviewer, finance, admin)

Staff sign in with a password **and** an authenticator code.

1. Go to <http://127.0.0.1:8000/login/staff/>.
2. Enter the staff email (for example `demo.ops@example.test`) and the
   password `seed_demo` printed. That password is only set on demo accounts it
   creates. If the accounts already existed, give them all a new one with
   `.\.venv\Scripts\python.exe manage.py seed_demo --yes --reset-passwords`,
   or use your own admin account.
3. The first time, the app shows a setup key. Add it to an authenticator app
   on your phone (Google Authenticator or Microsoft Authenticator: tap **+**,
   then **Enter a setup key**, type the key, choose **Time based**).
4. Type the 6-digit code from the app. It changes every 30 seconds.

Your own admin account is a staff account. It can't see the client pages. To
see what clients see, use `demo.owner@example.test`.

### No authenticator app to hand (local copy only)

In window 2, open the Django shell:

```powershell
.\.venv\Scripts\python.exe manage.py shell
```

At the `>>>` prompt, type these three lines, one at a time (put your staff
email in the second line):

```python
import pyotp; from apps.accounts.models import User; from apps.accounts.mfa import decrypt_secret
u = User.objects.get(email="you@example.com")
pyotp.TOTP(decrypt_secret(u.mfa_secret_encrypted)).now()
```

It prints the current code, like `'482913'`. Use it within 30 seconds. Type
`exit()` to leave. This only works on your own computer. On the live site,
always use the app on your phone.

## After merging a pull request: update your copy

In window 2:

```powershell
git pull
.\.venv\Scripts\python.exe -m pip install --require-hashes -r requirements\dev.txt
.\.venv\Scripts\python.exe manage.py migrate
```

Then restart the server in window 1 (Ctrl+C, then `runserver` again). The
`pip install` line only matters when the requirements changed. It's safe to run
every time.

## When something goes wrong

| What you see | What it means | What to do |
| --- | --- | --- |
| `127.0.0.1 refused to connect` | The server isn't running | Start it in window 1 |
| `That code is invalid or has expired` | Old code, wrong code, or no code was sent | Check window 1 for a new code; see "No code appears" above |
| `The module '.venv' could not be loaded` | You're in the wrong folder, or `.\` is missing | `cd C:\Users\HomePC\Documents\banda-app`, then use `.\.venv\...` |
| `fatal: not a git repository` | Wrong folder | `cd` into `banda-app` first |
| `fatal: 'in' does not appear to be a git repository` | Extra words were typed after `git pull` | Type only `git pull` |
| `CommandError: Re-run with --yes` | `seed_demo` wants confirmation | Run it again with `--yes` on the same line |
| `That code is invalid or has expired` on a **staff** account | You used the client page, `/login/` | Staff always use <http://127.0.0.1:8000/login/staff/> |
| The demo staff password doesn't work | The accounts existed before, so `seed_demo` didn't change them | `seed_demo --yes --reset-passwords` |
| `You have unapplied migrations` or a database error after `git pull` | The database needs updating | Run `migrate` (see above) |
| `Not Found: /meta.json` in window 1 | A browser extension asking for a file | Ignore it |
| `Unexpected token` after pasting into PowerShell | Output text got pasted along with the command | Press Ctrl+C and paste only the command |

## Good habits

- Never paste real passwords, setup keys or codes from the **live** site into
  chats, files or screenshots. Local demo keys are fine to share.
- Don't put real customer data into your local copy. Use `seed_demo`.
- Keep `.env` to yourself. It's in `.gitignore`, so Git never uploads it.
