# banda website (static)

The public marketing site for B & A Essentials, built as three standalone HTML pages:

| File | Page |
|---|---|
| `index.html` | One-page website: hero, services, how it works, pricing, about us, real stories, FAQs |
| `login.html` | Log in |
| `onboarding.html` | Five-step sign-up: account, email code, business details, plan, review |

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
git checkout main -- website/index.html website/login.html website/onboarding.html website/dashboard.html
mv website/*.html . && rmdir website
git commit -am "Update website" && git push origin gh-pages
```
