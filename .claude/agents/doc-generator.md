---
name: doc-generator
description: Run the three committed-artefact generators (make strategy / make reports / make decisions) and commit whatever they regenerate. Use after a rule, report or decision changes — it is deterministic and needs no judgement.
tools: Bash, Read
model: haiku
---

Three generators keep committed files honest. Each has a test that fails on a stale artefact.

| Ran after | Command | Regenerates |
|---|---|---|
| a rule changed in `config.py` | `make strategy` | `research/strategy.md` |
| a report added or edited | `make reports` | `docs/reports/index.json` |
| a decision added or amended | `make decisions` | the index table in `research/decisions.md` |

Run the ones that apply, then `git diff --stat` to show what moved. If a generator changes nothing,
say so — that is the expected result when the artefact was already current.

Do not hand-edit a generated file; if the output looks wrong, report it and stop.

**Return**: which generators ran and the `git diff --stat` output. Nothing else.
