# CLAUDE.md

Instructions for every Claude session in this repository.

## Before any work

- Read `README.md` (what each folder is, what runs where), `docs/LONG_HORIZON.md` (the design: roles, done-checks,
  watcher policy, judge, world model, evaluation), `docs/OWNER_BOT.md` (product repo split), and `PROGRESS.md`.
- The old design docs (`docs/PROJECT.md`, `docs/MEMROUTER.md`) and Horizon v1/v2 code are in the git tag
  `snapshot-pre-restructure`. Treat them as history and ideas, not as specs.

## Principles

- **Only what runs stays.** Don't add code nothing uses. When something stops being used, remove it.
- **Results must be real.** Every claim needs a baseline that could have beaten it (a plain agent, a notes file),
  hidden tests the agent never sees, and a confidence interval. Say plainly when a result is noise or negative.

## Decision rule

- When a choice isn't covered, pick the most reasonable default yourself, proceed, and log it in `PROGRESS.md`
  under "Decisions made" with a one-line reason.
- Stop and ask the owner only when the action:
  1. needs credentials, accounts or spending money;
  2. is hard to reverse (data deletion, public releases, changes to stored data);
  3. touches live Hermes (`/opt/embed-memory`, its plugins or config) or a folder a running benchmark uses;
  4. leaves you truly blocked.
- Hermes rails: food is cash on delivery only, travel is never paid, and Hermes never enters cards, CVVs, OTPs or
  UPI PINs.

## How to work

1. Plan first, with acceptance criteria.
2. Write tests for the behaviour being built.
3. Before finishing, confirm every criterion is met with the tests passing, or say plainly which one isn't.

## Development

- Set up with `uv venv -p 3.12 && uv pip install numpy pytest`. Run `.venv/bin/pytest`.
- Deploy to the Hermes server through S3 (`s3://embed-memory-deploy-861330781142/`), a presigned URL and an md5
  check, run over `aws ssm send-command` (instance `i-0d0e2f600c143e705`, ap-south-1). Never overwrite a folder a
  running benchmark uses; deploy to a new folder.

## End of every session

1. Update `PROGRESS.md`: Status, Decisions made, Open questions, Next step.
2. End with a plain-language summary: what changed, test results, open questions, next step.
