# B&A Essentials — AI Development, Security & Performance Guidelines

**Version 3 · 13 November 2026.** Replaces v2. The changes are listed in
section 0.

**Purpose:** This document is the standing engineering instruction for AI
coding assistants and human developers working on B&A Essentials ("banda"). Use
it throughout implementation, code review, testing and deployment. It is not a
recurring review task.

---

## 0. What changed from v2

| Area | v2 | v3 |
| --- | --- | --- |
| Project status | "Backend: not built yet" | Backend exists on `main` and was audited (section 1.1) |
| Hosting | AWS Lightsail, proposed | AWS Lightsail, **confirmed**; the Render setup in the repo is obsolete |
| Payments | Paystack | **Paystack** for clients billed in naira and **Flutterwave** for international clients; international pricing to be set later |
| Client sign-in | Passwords, password reset | **One-time codes** by WhatsApp, SMS or email; no client passwords (section 4.1) |
| Staff access | General least privilege | Rules for a human-delivered service: staff see only what their job needs, and every look is logged (section 4.3) |
| Audit log | Security events | **A complete audit trail for every user**, which each user can see for their own account (section 4.9) |
| Data protection | Not covered | Nigeria Data Protection Act 2023 duties and the Privacy Policy's promises (section 6) |
| Consent records | Not covered | Terms, renewal, marketing, phone-call and cookie choices are recorded (section 4.10) |
| Rate-limit cache | Implied Redis | Database cache on a single Lightsail server; Redis only when there's more than one |
| Numbering | Sections 9 and 10 each appeared twice | Renumbered; Supabase and Firebase notes removed |

---

## 1. Project status

Checked against the repository on 13 November 2026.

- **Public website and app preview:** static pages on GitHub Pages (branch
  `gh-pages`, source in `website/` on `claude/static-website`). They use
  sample data in the browser and send nothing to a server.
- **Backend:** a Django 5.2 project on `main` (14 apps: accounts,
  organizations, catalogue, files, tasks, quotes, reviews, billing,
  communications, operations, audit, core, website). 147 tests pass on
  PostgreSQL. See the Phase 0 audit for detail.
- **Payments:** no live integration. Paystack webhooks are verified; checkout,
  subscriptions and Flutterwave are not built.
- **Hosting:** none yet. Lightsail is the decision.
- **Source code:** GitHub, `Godleads1/ba-essentials`.
- **Priority:** security and performance together.

### 1.1 Phase 0 audit findings

Keep this table current. A finding is closed only when the fix is merged and
its regression test passes.

| ID | Severity | Finding | Status |
| --- | --- | --- | --- |
| F-01 | High | Django admin skipped the staff second factor | **Fixed** in PR #11 (merged 13 Nov 2026) |
| F-02 | High | No sign-up → payment → subscription path | Open, step 3 |
| F-03 | Medium | Admin sign-in form had no rate limit or lockout | **Fixed** in PR #11 |
| F-04 | Medium | Five staff roles see every client's tasks; staff views not logged | Open, step 6 and section 4.9 |
| F-05 | Medium | Payments not confirmed with the provider; no Flutterwave | Open, step 3 |
| F-06 | Medium | Legal review allowance not modelled | Open, step 3 |
| F-07 | Medium | Terms acceptance and consents not recorded | Open, step 5 |
| F-08 | Medium | No account closure or 90-day deletion | Open, step 5 |
| F-09 | Medium | Files on local disk only | Open, step 9 |
| F-10 | Low | No hash-locked dependency lockfile; dev-only pytest advisory | Open, step 9 |
| F-11 | Info | `main` history was replaced; older CI evidence can't be traced | Noted |

---

## 2. Core instructions to every AI coding assistant

You are working on a business application that handles customer accounts,
subscription payments, business records and confidential documents. People,
not software, do the client's work, so staff access to client data is part of
the normal workflow and must be controlled and recorded.

1. **Security and privacy are requirements, not optional enhancements.**
2. **Never assume a feature is secure because the UI hides it.** Enforce
   authentication, authorization, subscription entitlements and tenant
   boundaries on the server.
3. **Inspect before changing.** Read the repository, existing documentation,
   configuration and tests before proposing or editing files.
4. **Preserve working functionality.** Do not perform broad rewrites,
   redesigns, dependency upgrades or architectural migrations without a clear
   reason and approval.
5. **Make small, reviewable changes.** Prefer focused changes with tests over
   large batches of unrelated edits.
6. **Do not invent implementation status.** Clearly distinguish implemented,
   tested, planned, assumed and unverified work.
7. **Do not expose secrets.** Never print, commit, copy into documentation, or
   hardcode passwords, API keys, private keys, webhook secrets, database URLs or
   production credentials.
8. **Do not use real customer data in development or tests.** Use synthetic
   fixtures (`@example.test` addresses).
9. **Do not deploy, migrate production data, change DNS, enable live payments
   or modify production infrastructure without explicit authorization.**
10. **Do not merge your own pull requests.** Open them and stop; the project
    owner merges.
11. **Explain security-sensitive decisions.** State the threat being addressed,
    the control implemented and the test proving it works.
12. **Keep the product's promises.** The Terms of Service, Privacy Policy and
    Cookie Notice on the website are commitments. Code must not contradict
    them; if it must, stop and raise it.

If an instruction conflicts with security, privacy or data integrity, stop and
explain the conflict rather than silently weakening protection.

## 3. Architecture and technology

```text
Public website (static now; Django templates later)
    |
    v
Cloudflare (DNS, TLS, WAF, DDoS) ── origin open only to Cloudflare
    |
    v
AWS Lightsail instance: Nginx or Caddy → Gunicorn → Django
    |
    +---- Authentication: one-time codes (clients), password + TOTP (staff)
    +---- Authorization, tenant isolation, staff assignment scoping
    +---- Subscriptions, allowances and payment entitlements
    +---- Client and staff workflows
    +---- Audit log and notifications
    |
    +---- Lightsail managed PostgreSQL (private networking only)
    +---- ClamAV daemon (file scanning) on the instance
    +---- Private file storage: local disk now, Cloudflare R2 later
    +---- Paystack (naira) and Flutterwave (international)
    +---- Termii (SMS and WhatsApp codes and messages, to be confirmed)
    +---- Email provider (to be selected)
```

### Technology choices

| Layer | Choice | Status |
| --- | --- | --- |
| Backend | Django 5.2 LTS, Python 3.13 | In the repo |
| Frontend | Server-rendered templates, HTMX, Tailwind CSS | In the repo; the new designs exist only as static preview pages |
| Database | PostgreSQL (Lightsail managed) | Confirm plan, backups, restore test |
| Hosting | AWS Lightsail | **Confirmed**; replaces Render |
| Edge | Cloudflare | Configure origin protection |
| Rate-limit cache | Django database cache | Passes the production check; switch to Redis only with more than one server |
| File scanning | ClamAV (`clamd`) | Production refuses to start without it |
| File storage | Local disk, then Cloudflare R2 | R2 before a second server |
| Payments | Paystack (NGN), Flutterwave (international) | Test mode first; live only after approval |
| SMS / WhatsApp | Termii | Confirm account, sender IDs, WhatsApp support, delivery terms |
| Email | To be selected | Production refuses console email |
| Error monitoring | Sentry or equivalent | Confirm data scrubbing and retention |
| Browser testing | Playwright | Add end-to-end coverage |

Do not add Celery, a separate API framework, microservices, Kubernetes or other
infrastructure because they are popular. Add a component only when a
documented requirement justifies its cost and complexity.

## 4. Mandatory security requirements

### 4.1 Authentication and sessions

**Clients sign in with one-time codes; they have no password.** Codes go by
WhatsApp, SMS or email, as the client chooses. Staff sign in with a password
and an authenticator-app code.

One-time codes:

- 6 digits, random, stored only as an HMAC (as the existing challenge codes
  are), single use, bound to the account and the purpose.
- Expire after a few minutes; the Privacy Policy keeps the record for at most
  one day.
- A new code cancels the previous one.
- Limit codes sent per number, per email and per IP address, and wrong
  attempts per code and per account, with progressive delays. Code requests
  cost money and can be abused to spam people.
- Never reveal whether a phone number or email has an account: the same
  message and timing either way.
- Normalise phone numbers to one format (E.164) before storing or comparing.
- SIM-swap risk: offer clients an optional authenticator app (shown on the
  Profile & security page) and require a second check for sensitive actions,
  such as changing the phone number, email or payment method.
- Changing the phone number or email requires a code sent to the new contact,
  and a notice to the old one.
- Never put codes in logs, analytics, error reports or URLs.

Sessions:

- Secure, HttpOnly, SameSite cookies; HTTPS-only in production.
- Rotate the session identifier at sign-in and on privilege changes.
- Keep the existing credential-version binding and idle timeout. Define the
  "keep me signed in" lifetime and document it.
- Users can see their signed-in devices and sign them out (Profile & security);
  the server must actually revoke those sessions.
- Revoke all sessions when an account is disabled, closed or compromised.

Staff and administrators:

- Password (minimum 12 characters, existing validators) plus TOTP, enforced
  server-side for the staff app **and Django admin** (F-01).
- Django admin is reached only through the app's sign-in (F-03) and, once
  hosted, only from an allowlisted network or Cloudflare Access.

### 4.2 Authorization and role separation

- Enforce permissions in backend views, services and data access, never only in
  templates.
- Deny by default; check authorization on every object read, update, download,
  delete and export.
- Never trust a role, user ID, organisation ID, subscription status, price or
  permission supplied by the browser.
- Users cannot change their own role or privilege level.
- Administrative actions need stronger controls and an audit record.

### 4.3 Staff access to client data

banda is delivered by people, so staff read client documents every day. That
is the platform's largest insider-risk surface.

- **Need to know.** A staff member sees a client's request, messages and files
  only while they are assigned to it or their role needs it:
  - Specialists: their active assignments only (already enforced).
  - Reviewers: requests in their review queue, not every client's files.
  - Finance: invoices and payments, not request contents or documents.
  - Support: requests linked to the tickets they handle.
  - Operations: may triage all requests; opening one is logged.
- **Access ends** when the assignment ends, except read access the role still
  needs.
- **Every look is logged** (section 4.9), including list and search results
  that show client data, not only downloads.
- **Phone calls for clients** happen only with the authorisation the client
  gave on the request (who to call, and that it needs no personal action from
  them). Record it.
- Review staff access logs regularly; alert on unusual volumes (section 4.9).

### 4.4 Organisation and tenant isolation

banda serves many businesses. Tenant isolation is a critical boundary.

- Every tenant-owned record belongs to an organisation.
- Derive the active user and organisation from the authenticated session and
  verified membership. Never trust a client-supplied `organisation_id`,
  `tenant_id` or `user_id`.
- Scope every query, update, export, search and background job to the
  authorised tenant; check object-level permission even for hard-to-guess IDs.
- Test cross-tenant access for every major model and workflow, in CI.
- During the pilot, one person per business; the Team page is hidden. Keep the
  membership model; don't expose invitations until teams launch.

### 4.5 Payments and subscriptions

**The browser must never be trusted to confirm payment or grant paid access.**

Providers:

- **Paystack** for clients billed in naira; **Flutterwave** for international
  clients. The server chooses the provider from the client's billing country
  and currency, never from the browser.
- One adapter per provider: start checkout, verify a transaction, read and
  verify webhooks. Each provider's signature check is different; implement it
  exactly as that provider's current documentation describes.
- Plans need a price per currency. International prices are not set yet; keep
  Flutterwave switched off until they are approved.
- Use each provider's hosted checkout by redirect, so the strict
  Content-Security-Policy doesn't have to allow third-party scripts.

Required flow:

1. The client chooses a plan.
2. The backend loads the plan and price for the client's currency from the
   database.
3. The backend creates a payment record with a unique reference.
4. The backend starts checkout with the provider using server-held keys.
5. The client pays on the provider's page.
6. The return page shows "processing"; it does not activate anything.
7. The backend verifies the webhook signature over the unmodified raw body.
8. The backend confirms the transaction with the provider's verify call.
9. It checks status, reference, amount, currency and customer.
10. It processes the event once (database constraint) and records the result.
11. Only then is the subscription activated, in one transaction.
12. Every protected request checks the subscription on the server.

Subscription states and access rules (each state must have an explicit rule):

| State | Access |
| --- | --- |
| `PENDING_PAYMENT` | No paid features |
| `ACTIVE` | Full access within the plan's allowances |
| `CANCELLING` | Full access until the end date (30 days' notice, Terms 5.8) |
| `PAST_DUE` | Grace period per the approved billing policy (invoices: 5 business days, no late fees) |
| `SUSPENDED` | Read-only |
| `EXPIRED` / `CANCELLED` | Read-only access to files and invoices (Terms) |
| `PAYMENT_FAILED` / `REFUNDED` | No new paid work |

Allowances (Terms 3.2 and 3.3):

- General tasks per plan per month.
- Legal & IP reviews per plan (1 a quarter on Starter, 1 a month on Growth, up
  to 4 a month on Dedicated). Beyond the allowance, legal work is quoted first,
  and nothing is charged unless the client accepts.
- Specialized Add-Ons are quoted and billed separately.
- Consume allowances atomically (row lock or conditional update).

Also:

- Renewal is agreed explicitly at checkout; record the agreement (section
  4.10).
- Never store raw card details; use the providers' hosted, tokenised flows.
- Don't log payment secrets or card data.
- Define refund, chargeback and failed-payment handling before live payments.

### 4.6 Database security

- PostgreSQL is never reachable from the internet; private networking only.
- Separate least-privilege credentials for the application, migrations and
  operations where practical. The runtime role must not own the schema.
- Encrypted connections; secrets outside source control and logs.
- Transactions and constraints for payments, subscriptions, allowances and
  one-time actions.
- Automatic encrypted backups, and a restoration test before launch and on a
  schedule. A backup never restored is not a recovery plan.
- PostgreSQL Row-Level Security may be added as defence in depth; it never
  replaces Django authorization.

### 4.7 Files and documents

- Private storage only; never readable without authorization.
- Validate size, type and actual file signature (magic bytes), as the backend
  already does; generate storage names.
- Scan with ClamAV; quarantine until clean; never serve unscanned files.
- Short-lived signed downloads scoped to one file, issued only after an
  authorization check.
- Reject executable and macro-enabled formats.
- **Profile photos:** re-encode on the server (crop, resize to 256 px, strip
  EXIF such as GPS), store privately, show only to signed-in users with access.
- Record every document access (section 4.9).

### 4.8 Web attack protection

Use framework defaults and test for: SQL injection, XSS, CSRF, broken
object-level authorization (IDOR), SSRF, path traversal, session theft and
fixation, brute force and code spraying, privilege escalation, unsafe uploads,
enumeration and resource exhaustion, open redirects, host-header attacks, CORS
mistakes, and information leakage in errors and logs.

- Keep the strict Content-Security-Policy (`script-src 'self'`,
  `frame-ancestors 'none'`). Self-host fonts and icons when porting the new
  designs; don't loosen the policy for CDNs.
- Redirect only to relative paths or an explicit allowlist.
- Never pass untrusted input to shell commands or SQL strings.
- Production runs with `DEBUG=False` (the settings already enforce it) and
  never shows stack traces.

### 4.9 Audit log for every user

**Every user — client, staff member and administrator — has a complete,
attributable audit trail.** The `AuditEvent` model and 47 recorded actions are
the base; extend them as follows.

What is recorded, for every user:

| Area | Events |
| --- | --- |
| Sign-in | Code requested, signed in, wrong code, lockout, signed out, session revoked, authenticator added or removed |
| Account | Name, business, phone, email or photo changed; contact preferences and consents changed; data request made; closure requested or cancelled |
| Work | Request submitted, cancelled or changed; message sent; file uploaded or downloaded; quote accepted or rejected; deliverable approved or revision asked |
| Money | Plan chosen, payment started, verified or failed; renewal agreed; cancellation notice given; invoice paid |
| Staff access | Opening a client's request, list or search showing client data, file preview or download, export — with the client organisation |
| Administration | Role or permission changes, staff created or disabled, catalogue and price changes, admin-site changes |

Each event records: actor (user and type), the organisation affected, action,
target (type and ID), result (success, denied, error), time, correlation ID,
and a coarse device and network description (for example "Chrome on Android,
Lagos"). Store the IP address only as long as security needs require.

Who can see it:

- **Each user sees their own security activity** on Profile & security:
  sign-ins, wrong codes, devices, contact and security changes, consents and
  data requests.
- **Business owners** see their organisation's activity, including which staff
  role opened their requests and files, by role and not by personal staff
  details.
- **Administrators** can filter the full log by user, organisation, action and
  date. Viewing or exporting the audit log is itself recorded.

Rules:

- Never record passwords, codes, tokens, card data, secret keys or document
  contents, as the existing redaction already ensures.
- **Append-only:** the application's database role may insert audit rows but
  not update or delete them. Deletion happens only through the retention job,
  under a separate role.
- **Retention follows the Privacy Policy:** keep audit records at least as
  long as the longest record they relate to. The existing
  `purge_older_than(days)` must not delete events still needed for that; replace
  it with a schedule-driven job and test it.
- Index by actor and time, and by organisation and time, so one user's history
  loads quickly.
- Export a user's own events as part of an access request (section 6).
- Alert on repeated wrong codes, mass downloads, unusual staff access volumes,
  privilege changes and repeated authorization denials.
- Don't call the log immutable unless that property is implemented and tested.

### 4.10 Consent and agreement records

Store each with the user, organisation, the exact wording or document version,
the choice and the time:

- Terms of Service and Privacy Policy accepted at sign-up (version and date).
- Age and authority confirmation at sign-up.
- Automatic renewal agreed at checkout (plan, price, currency, first renewal
  date).
- Marketing consent: separate, unticked by default, with channel; withdrawable
  at any time.
- Phone-call authorisation on a request.
- Cookie choices (the `cookie_choices` cookie; record server-side once the
  backend sets cookies beyond strictly necessary ones).

### 4.11 Secrets and configuration

- Environment variables or a managed secret store; only `.env.example` in Git.
- Separate development, staging and production credentials.
- Never put secret keys in HTML or JavaScript. Paystack and Flutterwave public
  keys may appear in pages; secret keys never.
- Rotating `DJANGO_SECRET_KEY` makes staff authenticator enrolments unreadable
  (the key derives the encryption key); plan rotations.
- Scrub secrets, codes, tokens, personal data and payment details from logs and
  error reports.

## 5. Performance requirements

Performance is measured, not guessed.

Frontend:

- Minimise JavaScript and third-party scripts; self-host fonts and icons.
- Optimise and lazy-load images; minify and compress assets; cache static
  files.
- Keep the dashboard responsive on mobile and desktop.
- Never let private, user-specific pages into shared caches (the backend marks
  them `no-store`).

Backend and database:

- Paginate lists; remove N+1 queries (`select_related`, `prefetch_related`).
- Index by measured query patterns, including the audit-log indexes above.
- Timeouts and limits on expensive work; no unbounded queries.
- Cache only with a clear invalidation and privacy strategy; never share cache
  keys across tenants.

Initial targets (to measure, not guarantees):

| Metric | Target |
| --- | --- |
| Largest Contentful Paint | ≤ 2.5 s |
| Interaction to Next Paint | ≤ 200 ms |
| Cumulative Layout Shift | ≤ 0.1 |
| Typical application response | ≤ 500 ms |
| Dashboard initial load | ≤ 2.5 s |
| Availability objective | 99.9% |

## 6. Data protection (Nigeria Data Protection Act 2023)

The Privacy Policy states what banda promises; code must keep those promises.
Have a lawyer confirm the legal points below, including whether banda is a
controller "of major importance" under the Act.

- **Roles:** banda is the controller for account, billing, security and website
  data, and the processor for documents clients send for their tasks.
- **Data rights:** access, correction, deletion, restriction, objection,
  portability, withdrawing consent, and review of automated decisions. Clients
  request them from Profile & security or by email to the data protection
  contact (`dpo@bandaessentials.com`). Reply within 30 days. Verify identity
  proportionately. Each request and its outcome goes in the audit log.
- **Retention** (Privacy Policy, section 5): account and business profile
  while active and 90 days after closure; contracts, tax records and invoices 7
  years; trademark filings for the life of the registration plus 3 years; task
  documents 6 years after the task closes; support records 6 years; one-time
  codes 1 day; backups rotated within 2 days, with deletions reapplied after a
  restore. Build a scheduled deletion job and test it.
- **Account closure:** 30 days' notice for the plan, read-only access after it
  ends, deletion on the schedule above.
- **Breaches:** notify the Nigeria Data Protection Commission within 72 hours
  of becoming aware of a breach likely to put people's rights at risk (NDPA
  section 40), and tell affected people without undue delay when the risk is
  high. Keep an incident runbook and a breach register before launch.
- **Transfers outside Nigeria** (Cloudflare R2, payment providers, email,
  monitoring): list each provider and region, and check each transfer has a
  lawful basis under the Act.
- **AI features:** none exist. If added, they are optional, off by default,
  need separate consent and keep a human in control (Privacy Policy). Then
  apply section 8.5.
- **Minimise:** collect only what the service needs.

## 7. Infrastructure and deployment (Lightsail)

- Separate development, staging and production environments.
- Production behind HTTPS with Cloudflare DNS, WAF and DDoS protection; the
  Lightsail firewall allows web traffic only from Cloudflare, and SSH only by
  key from allowlisted addresses.
- Run the existing Docker image (Gunicorn) behind Nginx or Caddy, or on a
  Lightsail container service; never the Django development server.
- Lightsail managed PostgreSQL on private networking; automatic backups;
  restore tested.
- `clamd` on the instance; `process_outbox` and `scan_files` as systemd timers
  or cron.
- Database cache for rate limits on one server; Redis only for several.
- Health checks, process supervision, log forwarding and alerts.
- Controlled deployments with written rollback steps.
- Patch the operating system, runtime and dependencies.
- Remove `render.yaml` and the Render-specific notes once Lightsail staging
  works, in a reviewed change.

## 8. Expanded security verification

These checks apply throughout development and before every release. Reading
code isn't enough: provider dashboards, database settings and storage policies
must be inspected too.

### 8.1 Database access controls

- Least-privilege roles; the runtime role isn't a superuser or schema owner and
  can't update or delete audit rows.
- Test anonymous and cross-organisation reads and writes against real staging,
  not only mocks.
- Record what was inspected, where and when. Never claim a dashboard setting is
  verified from source code.

### 8.2 Authorization on every request

- Every private view, download, export, background job and admin action
  authenticates and authorises the actor for that specific record.
- Test anonymous requests, expired sessions, wrong roles, altered object and
  organisation IDs, and other tenants.
- Apply checks to list, detail, search, count, export, bulk and background
  paths; re-check permissions when a background job runs.
- Don't reveal whether another tenant's object exists.

### 8.3 Secrets and Git history

- Gitleaks in CI (already running) and over full history before releases.
- If a secret is exposed, rotate it first, then investigate; deleting the file
  isn't enough. Never paste secret values into reports or prompts.

### 8.4 Injection

- ORM parameterisation; review any raw SQL and dynamic identifiers separately;
  test hostile input.

### 8.5 AI features and prompt injection

Only if runtime AI features are added (none exist):

- Treat documents, emails, OCR text and tool results as untrusted data; they
  never grant permissions, approve work, change prices or start payments.
- Enforce tool permissions and tenant scope in code, not prompts.
- Human approval for consequential actions; no confidential documents to an AI
  provider without approved purpose, retention and consent.

### 8.6 Mass assignment

- Explicit forms and allowlists. Never accept client values for role, staff
  flags, owner, organisation, approval, price, balance, subscription, allowance
  or permissions. Log every role change.

### 8.7 Redirects, commands and SSRF

- Relative or allowlisted redirects only; no untrusted input in shell
  commands; restrict any server-side URL fetching and egress from file
  processors.

### 8.8 Dependencies

- Pin and hash-lock dependencies; verify package identity before adding one
  (beware lookalike names); scan for vulnerabilities in CI (already running);
  targeted upgrades with tests.

### 8.9 Browser integrity and headers

- Self-hosted, pinned assets; Subresource Integrity for any CDN asset that
  remains.
- Enforce the CSP; verify `X-Content-Type-Options`, `Referrer-Policy`,
  `Permissions-Policy`, HSTS and frame protection on deployed responses. The
  GitHub Pages preview can't send these headers; that is expected.

### 8.10 Errors and debug

- `DEBUG=False`; no stack traces, SQL, paths or versions in responses; minimal
  health endpoints.

### 8.11 Serialization

- No unsafe deserialization; validate JSON against schemas with size limits;
  validate token signature, audience and expiry.

### 8.12 Tokens and sessions

- Define expiry, rotation, revocation and audience for every token type:
  session, one-time code, authenticator, signed download, provider webhooks.
- No long-lived bearer tokens in browser storage. (The static preview keeps
  sample data in `localStorage`; the real app keeps nothing sensitive there.)

### 8.13 Storage verification

- Verify bucket privacy in the provider dashboard; test anonymous access,
  guessed keys, cross-tenant access, expired links and overwrite.

### 8.14 Uploads

- Allowlisted formats and sizes; signature checks; isolated parsing; reject
  macros and executables; quarantine until scanned.

### 8.15 Payment attacks

- Reject forged, unsigned, malformed, replayed and out-of-order webhooks for
  both providers; confirm with the provider; enforce idempotency with
  constraints; test concurrency, refunds, chargebacks and delayed responses.
  Never treat a browser redirect or screenshot as proof of payment.

### 8.16 Race conditions

- Transactions and row locks for subscriptions, allowances, payments, task
  assignment and one-time actions; explicit state transitions; idempotency
  keys for retries.

## 9. Engineering workflow for AI coding assistants

Before editing:

1. Read the relevant files, this guide and the audit findings.
2. Identify conventions, security and data-integrity implications.
3. State the intended change and the files affected.
4. Ask when a requirement is ambiguous or touches security, billing, privacy or
   production data.

While editing:

1. Make the smallest coherent change, following existing conventions.
2. Keep business logic out of templates and browser-only code.
3. Add or update tests with the change; prove a fix by showing the test fails
   without it.
4. Add migrations only when required and inspect them.
5. Don't change dependencies or framework versions silently; don't disable
   tests, checks or lint rules to pass a build.

After editing:

1. Run the full suite on SQLite and PostgreSQL, the migration check and
   `check --deploy` with production settings.
2. Run lint on changed files; review the diff for accidental changes and
   secrets.
3. Report exactly what changed and what was tested; list what wasn't.

Git:

- Work on a feature branch; open a pull request; **don't merge it yourself**.
- No force-push, history rewrites or branch deletion without approval.
- Never commit `.env`, credentials, database dumps or customer data.
- Include migration and rollback notes in pull requests.

## 10. Testing and release gates

Automated tests must cover: business rules; database and provider
integrations; authentication and authorization; tenant isolation; staff
assignment scoping; one-time code limits; payment verification and webhooks
for both providers; subscription states; allowances; uploads and document
access; audit-log completeness, append-only behaviour and retention; consent
records; data-request handling; and browser end-to-end journeys.

Mandatory negative tests — mark each passed, failed, not applicable (with
reason) or not yet tested:

- [ ] An anonymous user can't reach the dashboard or any private API.
- [ ] An unpaid or expired account can't use paid features.
- [ ] A browser-only payment success doesn't grant access.
- [ ] Invalid, duplicate, replayed and out-of-order webhooks are rejected or
      ignored safely, for Paystack and Flutterwave.
- [ ] A wrong amount, currency or customer is rejected.
- [ ] Tenant A can't read, change, search, export or download Tenant B data.
- [ ] A specialist can't open a request they aren't assigned to.
- [ ] Finance can't open request contents or documents.
- [ ] Staff without a second factor can't use the staff app or Django admin.
      **(Passed: PR #11.)**
- [ ] Django admin's own sign-in can't be used to check passwords. **(Passed:
      PR #11.)**
- [ ] Code requests and wrong codes are rate limited; responses don't reveal
      whether an account exists.
- [ ] Changing the phone or email requires a code to the new contact.
- [ ] Every staff view of client data creates an audit event.
- [ ] The application role can't update or delete audit events.
- [ ] Retention deletes what the schedule says, and nothing still required.
- [ ] Users can't inject role, owner, organisation, price, allowance or
      subscription fields.
- [ ] Unsafe uploads are rejected or quarantined; unscanned files can't be
      downloaded.
- [ ] Expired sessions, codes and signed links stop working.
- [ ] Redirect parameters can't send users off-site.
- [ ] Production never shows debug pages or stack traces.
- [ ] No secret appears in pages, Git history or CI output.
- [ ] Backups restore successfully.

Use OWASP ASVS 5.0.0 Level 2 as the verification framework and the OWASP Top
10 as an awareness checklist. Don't run intrusive tests against production
without authorization, scope and a safe window.

References: OWASP ASVS
(https://owasp.org/www-project-application-security-verification-standard/),
OWASP Top 10 (https://owasp.org/www-project-top-ten/), Django deployment
checklist (https://docs.djangoproject.com/en/stable/howto/deployment/checklist/),
Paystack (https://paystack.com/docs/), Flutterwave
(https://developer.flutterwave.com/), Cloudflare
(https://developers.cloudflare.com/), AWS Lightsail
(https://docs.aws.amazon.com/lightsail/), Lighthouse
(https://developer.chrome.com/docs/lighthouse/).

## 11. Order of work

Each step is its own reviewed pull request.

1. ~~Close the admin gap (F-01, F-03).~~ Done, PR #11.
2. ~~Update this guide to v3.~~ This document.
3. Payments and subscriptions (F-02, F-05, F-06): server-side checkout for
   both providers, verify calls, subscription states, allowances, 30-day
   cancellation; tested in each provider's test mode.
4. Sign-in with one-time codes: phone identifier, WhatsApp and SMS via Termii,
   email fallback, the rules in section 4.1.
5. Records the legal pages promise (F-07, F-08): consents, data requests,
   account closure, retention job.
6. Staff access scoping and the per-user audit log (F-04, section 4.9).
7. Lightsail staging: instance, managed PostgreSQL, Cloudflare, ClamAV,
   database cache, backups with a restore test.
8. Port the new designs into Django templates, page by page, with permission
   tests.
9. Cloudflare R2 storage and a hash-locked dependency lockfile (F-09, F-10).

## 12. Response format for implementation tasks

At the end of each task, report:

1. What you inspected
2. What you changed
3. Security impact
4. Performance impact
5. Tests run and their actual results
6. Migrations or configuration changes
7. Remaining risks and unverified assumptions
8. Recommended next step

Label status clearly: **Verified** (inspected or tested), **Implemented, not
yet verified**, **Planned**, **Blocked**. Never call the platform "secure" or
"enterprise-grade" on the strength of a checklist; report the controls and the
evidence.

## 13. Security audits and evidence

For each significant feature and before each release:

1. Map data flows, trust boundaries, roles, tenants, providers and sensitive
   data.
2. Identify threats and abuse cases before building.
3. Inspect code, dependencies, tests, configuration and deployment settings.
4. Inspect provider dashboards, or get a human-confirmed configuration export.
5. Run focused checks and negative tests in staging.
6. Have high-risk findings checked independently; an AI agent's finding isn't
   confirmed just because it generated it.
7. Report each finding with: ID, title, severity, confidence, blast radius,
   evidence (secrets redacted), reproduction steps, impact, fix, regression
   test and verification result.
8. Re-test fixes; record accepted risks with an owner and expiry date.

Third-party audit tools (such as the Cloudflare security-audit skill) are
optional aids: verify their source, run them read-only on a clean branch with
no production credentials or customer data, and confirm their findings
independently.

## 14. Launch conditions

banda must not take real payments or confidential documents until:

- Client sign-in (one-time codes) and staff sign-in (password + TOTP) are
  implemented and tested, including rate limits.
- Server-side authorization, tenant isolation and staff assignment scoping are
  tested.
- Paystack (and Flutterwave, if international clients are accepted at launch)
  pass test-mode verification, webhook and subscription tests.
- The per-user audit log is complete, append-only for the application and
  retained per the Privacy Policy.
- Consent records, data requests, account closure and the retention job work.
- Private document storage, scanning and access checks are in place.
- Production secrets and Lightsail infrastructure are configured and reviewed.
- Backups have been restored successfully in a test.
- An incident and breach-notification runbook exists.
- Critical security tests pass and monitoring with alerts is live.
- Production deployment is reviewed and explicitly approved.

Release blockers at any time: a confirmed critical or high vulnerability;
cross-organisation data access; an exposed, unrotated secret; payment forgery
or wrong activation; public customer documents or backups; an authentication or
staff-privilege bypass; production debug output; accessible unscanned files; or
missing evidence for a critical external setting. A risk exception needs a
reason, scope, compensating control, owner and expiry date; tenant isolation,
payment integrity and document protection are never waived for schedule.

---

**Standing instruction:** Use this document as the engineering guide
throughout B&A Essentials development. Revisit it when architecture decisions
change. Don't treat a planned control as implemented until code,
configuration and tests show it.
