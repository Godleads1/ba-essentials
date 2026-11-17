# Incident and data breach response plan

What B & A Essentials does when something goes wrong with security or personal
data: who leads, what to do in the first hour, who must be told and by when,
and what to record. Keep it short enough to follow under pressure.

> **Status: draft for review.** The legal points below summarise the Nigeria
> Data Protection Act 2023 (NDPA), section 40, from published legal commentary.
> Have the company's lawyer confirm them, and check the Nigeria Data
> Protection Commission (NDPC) for its current reporting channel, before launch.
> No secrets belong in this file. The repository is public.

## 1. What counts as an incident

An **incident** is anything that threatens the confidentiality, integrity or
availability of the platform or the data in it. Examples:

- someone signs in to an account that isn't theirs
- a client's file, message or invoice is seen by the wrong business
- a staff device or password is lost or exposed
- a secret leaks (an API key, the Django secret key, the database URL, a
  payment webhook secret), for example in a commit, a screenshot or a chat
- suspicious payments, refunds or webhook calls
- malware found in an upload
- the site is down, or data is deleted or changed without authority

A **personal data breach** is a breach of security that leads to, or is
reasonably likely to lead to, the accidental or unlawful destruction, loss,
alteration, unauthorised disclosure of, or access to personal data. **Treat
every incident as a possible breach until you've ruled it out.**

## 2. Who does what

| Role | Who | Does |
| --- | --- | --- |
| **Incident lead** | The founder (until a named deputy exists) | Decides, coordinates, signs off every notification |
| **Data protection officer (DPO)** | dpo@bandaessentials.com | Assesses risk to people, drafts NDPC and client notices, keeps the breach register |
| **Technical lead** | The developer on call | Contains, investigates and fixes; preserves evidence |
| **Communications** | Support (support@bandaessentials.com) | Answers clients with the approved wording only |

Keep a private contact sheet, **outside this repository**, with each person's
phone number, plus the contacts for the hosting provider, Paystack,
Flutterwave, Termii and the email provider.

## 3. Severity

| Level | Examples | Response |
| --- | --- | --- |
| **High** | Client data seen by another business or outsider; leaked secret; staff account taken over; payment fraud | Start now, any time of day. Lead and DPO informed within 1 hour. |
| **Medium** | One account compromised with no sign of data access; malware caught by the scan; partial outage | Start the same day |
| **Low** | Failed attack with no access gained; a near miss | Record it, fix it in the normal way |

If you're unsure, treat it as High.

## 4. The first hour: contain

Write down the time you became aware. **The 72-hour clock starts then.**

1. **Don't delete anything.** Keep logs, the audit trail
   (`/staff/audit/`), screenshots and the affected records.
2. **Stop the harm** with the smallest action that works:
   - *One account taken over:* deactivate it in admin (untick **Active**),
     which ends its sessions. For a staff account, a password reset also
     raises its credential version, which signs it out everywhere.
   - *Many accounts, or unsure:* sign everyone out. Sessions are stored in the
     database, so on the server:
     `python manage.py shell -c "from django.contrib.sessions.models import Session; Session.objects.all().delete()"`
   - *A secret leaked:* rotate it with its provider, then update it in the
     server's secret manager and restart. Rotating `DJANGO_SECRET_KEY` also
     signs everyone out. Never paste the new value into a chat or a file.
   - *Payments look wrong:* set `PAYMENTS_ENABLED=false` and restart, then
     check with Paystack or Flutterwave.
   - *A business's records must not be deleted* (investigation or dispute):
     tick **Legal hold** on the organisation in admin. The retention job then
     skips it.
   - *A bad file:* block the download, and keep the file for investigation.
3. **Tell the incident lead and the DPO.**

## 5. Assess (within 24 hours)

Answer, and record the answers:

- What happened, and when did it start?
- Which data: account details, business documents, messages, invoices,
  payment references? Was any of it sensitive? (We never hold card numbers.)
- Whose data, and roughly how many people and records?
- Was the data actually seen or taken, or only exposed?
- Do our protections lower the risk? For example, files are private and every
  download is checked and logged, and sign-in codes expire after 10 minutes.
- Is it still happening?

Then decide the risk to the **people** affected (not to the company):

- **No real risk**, for example a stolen laptop with an encrypted disk:
  record it, no notice needed.
- **Risk to people's rights and freedoms:** tell the NDPC (section 6).
- **High risk**, for example fraud, identity theft, embarrassment, or loss of
  confidential business documents: also tell the people affected (section 7).

## 6. Tell the regulator: within 72 hours

Under NDPA section 40(2), a controller must notify the **NDPC within 72 hours**
of becoming aware of a breach that is likely to result in a risk to people's
rights and freedoms. If not everything is known yet, send what you have and
follow up in phases. Don't wait for the full picture.

The notice should cover:

- what happened, and when it was discovered
- what data, and approximately how many people and records
- the likely consequences
- what we've done, and will do, to contain it and to reduce harm
- the DPO's contact details

## 7. Tell the people affected: without delay

Under NDPA section 40(3), if the breach is likely to result in a **high risk**,
tell the people affected **immediately**, in plain language, including what
they can do to protect themselves (for example: watch for messages pretending
to be us; we never ask for codes, passwords or card details). If contacting
each person directly would take disproportionate effort, a public notice is
allowed. The NDPC can also make its own announcement if ours isn't adequate.

For a client business's own documents, also tell that **business** directly:
they may have their own duties to their customers.

Use in-app notifications and email. Support answers questions with the
approved wording only.

## 8. Our suppliers

Paystack, Flutterwave, Termii, the hosting provider and the email provider
process data for us. Their contracts should require them to tell us promptly
about a breach. When one does, start this plan at section 4.

## 9. Record every breach

NDPA section 40 requires a record of **every** personal data breach, including
ones that didn't need notifying: the facts, its effects and the remedial
action taken. Keep the register in a private place, not in this repository.
Use these columns:

| Field | Example |
| --- | --- |
| Reference | INC-2026-001 |
| Discovered (date and time) | 2026-12-03 14:20 WAT |
| Reported by | Support |
| What happened | Invoice PDF emailed to the wrong client |
| Data and people affected | 1 invoice, 1 client business |
| Risk decision and why | Risk (business financial data); not high |
| NDPC notified | Yes, 2026-12-04 10:00 WAT; reference … |
| People notified | Client informed 2026-12-03 16:00 WAT |
| Containment and fix | Recipient confirmed deletion; email template fixed |
| Lessons and follow-ups | Add a check before sending; owner, due date |
| Closed | 2026-12-10 |

## 10. Afterwards

Within two weeks, hold a short, blameless review: what happened, why, what
worked, and what we'll change. Turn each change into a tracked task with an
owner. Update this plan if it didn't work in practice.

Run a 30-minute practice ("tabletop") exercise before launch and twice a year,
for example: "A client says they can see another business's invoice."

## 11. Quick checklist

- [ ] Time of awareness written down (72-hour clock started)
- [ ] Evidence kept; nothing deleted
- [ ] Harm stopped (account, sessions, secret, payments, legal hold)
- [ ] Incident lead and DPO told
- [ ] Risk to people assessed and recorded
- [ ] NDPC notified within 72 hours, if there's a risk
- [ ] People and client businesses told without delay, if the risk is high
- [ ] Breach register updated
- [ ] Review held; follow-up tasks created
