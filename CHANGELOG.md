# Changelog

## Unreleased

- `query_batch`: several independent queries in one skill invocation, parsing the dataset
  once. Takes `analyse_plan` batches with status `ready` directly.
- Answered queries return an `evidence_id`; the envelope is kept unchanged in a bounded,
  process-local store.
- `analyse_bundle`: assemble cited results and findings, split by claim class, into a
  reproducible bundle with a Markdown rendering.
- `analyse_plan` always names a profile (the server default when none is given). Without
  one, step availability was unknown and the planner offered no batches.

## 0.1.0 - unreleased

First version.

- Tools over the linked-archi-apm skill owners: dataset connect and candidates; profile
  list, show, recommend and verify; catalogue list and show; query render, run, literal and
  lint; analysis patterns and plan; SHACL validation; doctor.
- Refusals come back as `status: "refused"` with alternatives, never as empty results.
- Each skill's SKILL.md and references are exposed as `linked-archi://skills/...`
  resources; an `investigate` prompt.
- stdio and streamable HTTP transports. Over HTTP, clients are confined to the configured
  dataset unless `--allow-client-paths` is given, and a non-loopback bind requires
  `$LINKED_ARCHI_MCP_TOKEN`.
- Skills resolved from `--skills-dir`, `$LINKED_ARCHI_SKILLS_DIR`, or the
  `vendor/linked-archi-apm` submodule pinned at v0.10.0.
