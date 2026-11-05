# Changelog

## 0.1.0 - unreleased

First version. Bundles the linked-archi-apm skills at v0.10.0.

### Tools, resources and prompt

- Tools over the linked-archi-apm skill owners: dataset connect and candidates; profile
  list, show, recommend and verify; catalogue list and show; query render, run, batch,
  literal and lint; analysis patterns, plan and bundle; SHACL validation; doctor.
- Refusals come back as `status: "refused"` with alternatives, never as empty results.
- `query_batch` runs several independent queries in one skill invocation, parsing the
  dataset once. It takes `analyse_plan` batches with status `ready` directly.
- Answered queries return an `evidence_id`; the envelope is kept unchanged in a bounded,
  process-local store. `analyse_bundle` assembles cited results and findings, split by
  claim class, into a reproducible bundle with a Markdown rendering.
- `analyse_plan` and the catalogue tools always name a profile (the server default when
  none is given), so availability is never unknown.
- Each skill's SKILL.md and references are exposed as `linked-archi://skills/...`
  resources; an `investigate` prompt.

### Transports

- stdio and streamable HTTP. Over HTTP, clients are confined to the configured dataset
  unless `--allow-client-paths` is given, and a non-loopback bind requires
  `$LINKED_ARCHI_MCP_TOKEN`. `GET /healthz` answers without the token.

### Distribution

- The wheel bundles the skills, so `uvx linked-archi-mcp` needs nothing else. pyoxigraph
  is a dependency; SHACL validation is the `shacl` extra.
- Skills resolve from `--skills-dir`, `$LINKED_ARCHI_SKILLS_DIR`, the bundled copy, or the
  `vendor/linked-archi-apm` submodule of a source checkout.
- Container image on GHCR: streamable HTTP by default, stdio when given arguments,
  unprivileged user.
- Listed in the MCP Registry as `io.github.linked-archi/linked-archi-mcp`.
