# Contributing

See [`CLAUDE.md`](./CLAUDE.md) for the full working agreement (conventions, issue/board hygiene, IBKR/runtime notes). This is the short version.

## Dev setup
```bash
make setup     # creates .venv, installs the package + dev tools (Python 3.11)
make check     # runs all CI gates: lint, format-check, type-check, test
```

## Workflow
- Trunk-based. `main` is protected — **all changes via PR**; CI (`lint-typecheck-test`) must pass; squash-merge; linear history.
- Branch names: `feat/…`, `fix/…`, `chore/…`, `spike/…`, `docs/…`.
- Conventional commit/PR titles (`feat:`, `fix:`, `chore:`, `spike:`, `docs:`).
- Link issues: `Closes #N` when the PR completes the issue, else `Refs #N`; reference the Phase-1 epic (`Refs #1`).
- Every unit of work is a GitHub issue, tracked on [project #3](https://github.com/users/bennetwi92/projects/3).

## Before you push
Run `make check` locally — CI runs the same gates and will block the merge otherwise.

## Secrets
Never commit credentials. Local config lives in `.env` (gitignored); copy `.env.example`. CI uses GitHub Actions secrets; the VPS holds runtime secrets in its own environment.
