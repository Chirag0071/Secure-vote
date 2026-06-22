# Deploying to Render

Render hosts the FastAPI app. Render does **not** offer managed MySQL (only
Postgres natively), so the database lives on **Aiven**, which currently has
a genuine always-free MySQL tier (one free service per service type, no
credit card). The app and the database talk to each other over the internet,
over TLS.

Good news either way: Render gives every web service free HTTPS on its
`*.onrender.com` domain automatically, which matters here specifically
because browsers refuse camera access (`getUserMedia`) over plain HTTP on
any domain that isn't `localhost`.

---

## 1. Push the project to GitHub

Render deploys from a git repo.

```bash
cd secure-vote
git init
git add .
git commit -m "Initial commit"
# create a repo on GitHub, then:
git remote add origin https://github.com/<you>/secure-vote.git
git push -u origin main
```

Double check `.env` and `secret.key` are **not** in that commit — they're
already in `.gitignore`, but `git status` is worth a glance before pushing.

## 2. Create the database on Aiven

1. Sign up at aiven.io, create a project, then create a **free MySQL** service.
2. Once it's up, the console gives you: host, port, user (usually `avnadmin`),
   password, and a default database name (usually `defaultdb`).
3. Download the **CA certificate** Aiven provides (a `ca.pem` file) — this is
   a public certificate, not a secret, so it's fine to commit it. Save it as
   `aiven-ca.pem` in the project root and `git add` / commit / push it.

Use the default database Aiven gives you (e.g. `defaultdb`) rather than
trying to create a new one — simpler, and free-tier services are typically
scoped to a single database anyway.

## 3. Create the Web Service on Render

1. New → Web Service → connect your GitHub repo.
2. **Build command:** `pip install -r requirements.txt`
3. **Start command:** `uvicorn app:app --host 0.0.0.0 --port $PORT`
   (Render assigns the port dynamically via `$PORT` — don't hardcode 5000/8000 here)
4. **Environment variables** (Render dashboard → Environment):

| Key | Value |
|---|---|
| `SECUREVOTE_DB_HOST` | the Aiven host |
| `SECUREVOTE_DB_PORT` | the Aiven port |
| `SECUREVOTE_DB_USER` | `avnadmin` (or whatever Aiven gave you) |
| `SECUREVOTE_DB_PASSWORD` | the Aiven password |
| `SECUREVOTE_DB_NAME` | `defaultdb` (or your Aiven default db) |
| `SECUREVOTE_DB_SSL_CA` | `aiven-ca.pem` |
| `SECUREVOTE_SESSION_SECRET` | a long random string (see below) |
| `SECUREVOTE_JWT_SECRET` | a different long random string |
| `SECUREVOTE_FERNET_KEY` | a Fernet key (see below) -- **required** on Render |
| `SECUREVOTE_ADMIN_PASSWORD` | a real admin password |

`SECUREVOTE_HTTPS_ONLY` doesn't need to be set — it defaults to `true`,
which is correct for Render since it serves HTTPS.

Generate fresh secrets rather than reusing the local dev ones:
```bash
python -c "import secrets; print(secrets.token_hex(32))"          # session/JWT secrets
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"   # SECUREVOTE_FERNET_KEY
```

`SECUREVOTE_FERNET_KEY` encrypts face encodings at rest. Without it set
explicitly, the app falls back to writing a key file to disk — which works
locally, but Render's filesystem is ephemeral, so that file (and every
registered voter's face data) would be wiped on the next redeploy. Set it
explicitly as an env var and this isn't a problem.

5. Deploy. First build will take a few minutes (mostly `opencv`/`numpy`
   downloading prebuilt wheels — `dlib-bin` in requirements.txt means dlib
   itself installs as a prebuilt wheel too, so it won't try to compile from
   source and hit Render's build timeout).

## 4. First run

On startup, `init_db()` runs automatically and creates the tables (`voters`,
`candidates`, `ballots`, `audit_log`, `admins`) inside Aiven's database, plus
the default admin account and two sample candidates — same as local dev.

Visit `https://<your-app>.onrender.com`, register a face, vote, then log into
`/admin/login` with the admin password you set.

## Things worth knowing about Render's free tier

- **Cold starts**: free web services spin down after inactivity and take
  ~30-60s to wake back up on the next request. Fine for a demo, worth
  mentioning if you're presenting it live (or just visit the URL a minute
  before you need it).
- **Ephemeral filesystem**: anything written to disk at runtime is lost on
  every redeploy/restart. This is exactly why `SECUREVOTE_FERNET_KEY` is set
  as an env var above rather than left to the file-based fallback.
