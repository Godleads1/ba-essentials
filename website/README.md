# banda website (static)

The public marketing site for B & A Essentials, built as three standalone HTML pages:

| File | Page |
|---|---|
| `index.html` | One-page website: hero, services, how it works, pricing, about us, real stories, FAQs |
| `login.html` | Log in |
| `onboarding.html` | Six-step sign-up: account, email code, business details, plan, review, payment |
| `dashboard.html` | Client dashboard (sample data) |
| `submit-task.html` | Submit a Task: service, details and files, review, confirmation |
| `my-tasks.html` | My Tasks: all requests with search, filters, status guide and a ⋯ menu per request |
| `task.html?id=…` | Request detail: details, files, quote, messages, progress timeline, deliverables, cancel |
| `tasks-data.js` | Sample task data shared by My Tasks and Request detail (the app will replace it with its API) |
| `catalogue.html` | Service Catalogue: categories, availability, search across services, details panel with request links |
| `catalogue-data.js` | The service list (from the Task Bank), shared by the Catalogue and Submit a Task |

It is published with GitHub Pages from the `gh-pages` branch, which holds a copy of this folder.

## Status

These pages are a design preview. The forms check what people type but send nothing, and
any six digits pass the email code step. Styling loads from the Tailwind Play CDN, which is
meant for prototypes; the Django app builds the same classes with the Tailwind CLI.

Before launch:

- Replace the three sample quotes in "Real stories" with real, consented client quotes.
- Add contact details for "Contact Sales" and "Talk to Our Team", and the social profile links.
- Confirm the FAQ answers (refunds, going over the task limit, cancelling).
- Link the Terms of Service and Privacy Policy, and connect login and sign-up to the app.
- Replace the sample data in `dashboard.html` with the client's real tasks and usage.

## Quality checks

Every change to these pages is checked with:

- the W3C HTML validator (Nu Html Checker): no errors
- axe-core accessibility rules (WCAG 2.2 AA and best practice): no violations, on laptop and phone widths
- ESLint on each page's script: no errors or warnings
- Chromium, Firefox and WebKit (Safari's engine): no script errors, no sideways scrolling, and the
  full sign-up → payment → dashboard flow completes, at laptop and phone sizes

Styles are compiled ahead of time into `app.css` (no Tailwind script runs in the browser), and icons
in the page markup are built in as SVG, so pages appear complete on first paint with no flicker.
The icon script (Lucide 1.60.0, pinned) only draws the few icons created by JavaScript; if it fails
to load, the pages still work.

## Building

After editing any page, rebuild from `website/build/`:

```bash
npm install
npm run build   # bakes icons into the pages, then compiles app.css
```

The colours and fonts live in `build/tailwind.config.js`.

## Payments (Paystack)

Step 6 of `onboarding.html` takes payment with Paystack's popup (Inline JS v2), by card,
bank transfer or USSD. With no key set, the step shows a preview notice and lets people
finish without paying. To switch real payments on, edit the `PAYSTACK` object in the
page's script:

```js
const PAYSTACK = { publicKey: 'pk_test_...', planCodes: { starter: 'PLN_...', growth: 'PLN_...', dedicated: 'PLN_...' } };
```

- Use the **public** key only (`pk_test_` while testing, `pk_live_` when live). The secret
  key never goes in a web page.
- Create the three monthly plans (₦65,000, ₦97,500, ₦260,000) in the Paystack dashboard and
  paste their plan codes, so customers are subscribed and charged each month. Without plan
  codes, Paystack takes a one-off payment.
- The popup's success message is not proof of payment. The Django app must confirm each
  payment (the `charge.success` webhook, or the verify endpoint with the reference) before
  switching a plan on. The page sends the plan name in the transaction metadata for this.

## Updating the live site

Edit the files here, then copy them to the `gh-pages` branch:

```bash
git checkout gh-pages
git checkout main -- website/index.html website/login.html website/onboarding.html website/dashboard.html website/submit-task.html website/my-tasks.html website/task.html website/tasks-data.js website/catalogue.html website/catalogue-data.js
mv website/*.html . && rmdir website
git commit -am "Update website" && git push origin gh-pages
```
