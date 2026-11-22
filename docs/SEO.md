# Search engines (SEO)

What the site does for search engines, and what the owner does outside the code.
No setting guarantees a ranking: Google weighs how useful the pages are, how long
the site has been live, links from other sites and reviews. The code makes sure
nothing technical holds the site back.

## Built in

| What | Where |
| --- | --- |
| Page title and search description, editable for the home page and editor pages | Website > Home page; each page's search settings |
| Canonical links built from `APP_BASE_URL` (never the request's Host header) | `templates/banda/public/base.html` |
| Link previews (Open Graph) on every public page, with a default share image | `static/banda/public/og-default.png` |
| `robots.txt` (keeps `/app/`, `/staff/`, sign-in and webhooks out) and `sitemap.xml` | `apps/website/seo.py` |
| Structured data on the home page: the business, the website and the questions | `apps/website/seo.py` |
| Site icons Google can show next to results, and `/favicon.ico` | `static/banda/public/` |
| `lang="en-NG"`, one main heading per page, `noindex` on previews, errors and private areas | templates, `apps/core/middleware.py` |

Tests: `tests/test_seo.py`.

## Server settings

| Setting | Value |
| --- | --- |
| `APP_BASE_URL` | The address people should land on, e.g. `https://bandaessentials.com`. Pick with or without `www` and redirect the other one to it (Cloudflare can do this). |
| `GOOGLE_SITE_VERIFICATION` | The `content` value of the HTML-tag method in Google Search Console |
| `BING_SITE_VERIFICATION` | The `content` value from Bing Webmaster Tools |
| `BUSINESS_CITY`, `BUSINESS_REGION`, `BUSINESS_PHONE` | Optional. Shown to Google only when set; use the same details as the Google Business Profile |
| `SEO_SAME_AS` | Optional. Comma-separated links to the business's LinkedIn, Instagram, X or Facebook pages |

## After launch (owner)

1. **Google Search Console:** add the site, verify with the HTML tag (the setting above), and submit `https://<your address>/sitemap.xml`. Check **Pages** weekly for the first month.
2. **Bing Webmaster Tools:** import from Search Console. Bing also feeds other search engines.
3. **Google Business Profile:** create it with the same name, phone and city as the settings. Ask happy clients for reviews there.
4. **Rich results test:** paste the home page address into Google's Rich Results Test and fix anything it flags.
5. **Content:** publish useful pages in the editor (an About page, a page per service area, answers to the questions clients ask), and keep the home page questions current. This matters more than any setting.
6. **Links:** list the business in Nigerian business directories, partner sites and social profiles, all pointing to the same address.
7. **Speed:** keep Cloudflare's compression and caching on. The home page is about 300 KB with no images; keep uploaded images small.
