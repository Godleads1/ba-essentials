# Instructions for AI coding assistants

Read [docs/AI_DEVELOPMENT_GUIDELINES.md](docs/AI_DEVELOPMENT_GUIDELINES.md)
before changing anything. It is the standing engineering guide for this
repository: security, privacy, payments, testing and the order of work.

In short:

- Inspect before changing; make small pull requests with tests.
- Enforce every permission on the server; never trust the browser.
- Never commit secrets or real customer data.
- Don't deploy, enable live payments or merge your own pull requests.
- Report what you changed, what you tested and what you didn't.

Security notes: [docs/SECURITY.md](docs/SECURITY.md). Deployment notes:
[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) (Render, being replaced by AWS
Lightsail).
