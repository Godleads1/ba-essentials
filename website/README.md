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

## Updating the live site

Edit the files here, then copy them to the `gh-pages` branch:

```bash
git checkout gh-pages
git checkout main -- website/index.html website/login.html website/onboarding.html
mv website/*.html . && rmdir website
git commit -am "Update website" && git push origin gh-pages
```
