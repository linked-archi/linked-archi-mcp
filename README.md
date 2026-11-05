# linked-archi-mcp

An [MCP](https://modelcontextprotocol.io) server for read-only analysis of architecture
knowledge graphs: ArchiMate, BPMN, C4/Structurizr, Backstage and LeanIX models converted to RDF
by the [Linked.Archi converters](https://gitlab.com/linked-archi/linked-archi-tools/converters/converters).

It is a thin adapter over the
[linked-archi-apm](https://github.com/linked-archi/linked-archi-apm) agent skills. The skills
own everything that matters: the profile-resolved SPARQL template library, capability
gating, read-only enforcement, provenance and the analysis patterns. This server runs
their executables as subprocesses through the same versioned JSON contracts the skills
use to call each other. It never imports their Python code and never re-implements a
rule. That way the skills stay installable on their own, and the server can't drift from
them.

**A refusal is an answer, not an error.** When the dataset's profile cannot support a
template, the tool returns `status: "refused"` with the reason and an alternative. It
never returns an empty result, which would read as "nothing exists".

```json
{
  "status": "refused",
  "message": "Template 'core/dependents-direct' cannot run against profile 'linked-archi-qualified':\n  - capability 'direct_rel_triples' is False ...",
  "alternatives": ["core/dependents-qualified", "core/neighbours-qualified"]
}
```

## What it exposes

| Tool | Skill owner | What it does |
|---|---|---|
| `dataset_connect` | connect | Load the dataset and report quads, named graphs, and inputs that lost graph identity |
| `dataset_candidates` | connect | Candidate RDF files near a directory (only when client paths are allowed) |
| `profile_list` / `profile_show` | profile | Bundled profiles; one fully resolved profile |
| `profile_recommend` | profile | Name the profile that fits the dataset's shape |
| `profile_verify` | profile | Check a profile's claims against the dataset (`verified` or `drift`) |
| `catalog_list` / `catalog_show` | query | Templates gated by the profile; one template's parameters and `does_not_prove` |
| `query_render` | query | Template → SPARQL, without executing |
| `query_run` | query | Execute a template; result envelope with provenance |
| `query_literal` | query | Execute hand-written read-only SPARQL (updates, `LOAD` and `SERVICE` refused) |
| `query_lint` | query | Is this SPARQL read-only? |
| `analyse_patterns` / `analyse_plan` | analyse | Route a question to a pattern and get ordered steps (executes nothing) |
| `validate_run` | validate | SHACL in-process, with target-class coverage |
| `doctor` | — | Skill locations and versions, dependencies, configured target |

Every tool is annotated read-only. `query_run` results return at most `max_rows` rows,
default 200. `row_count` always gives the full count, and `rows_omitted` says how many
were cut.

**Resources:** each skill's `SKILL.md` and its `references/**/*.md`, under
`linked-archi://skills/<skill>/<path>`. These documents explain the graph's conventions
(qualified relationships, named graphs, the evidence model) and make a client a better
analyst.

**Prompt:** `investigate(question)`. It connects and verifies, plans, runs each step, and
answers in three parts: facts, inference and unknowns.

Source acquisition (`la-source`) and evidence bundling (`la-analyse bundle`) are not
exposed yet. They fetch over the network and write files, which needs its own design for
a server that can run over HTTP.

## Install

Python 3.11+. The skills run as subprocesses of the server's own interpreter, so their
dependencies are installed alongside it:

```bash
git clone --recurse-submodules https://github.com/linked-archi/linked-archi-mcp
cd linked-archi-mcp
pip install -e '.[all]'      # pyoxigraph for local RDF, pyshacl for validation
```

### Where the skills come from

Resolution stops at the first match and never falls back:

1. `--skills-dir DIR`
2. `$LINKED_ARCHI_SKILLS_DIR`, the same variable the skills use to find each other
3. the `vendor/linked-archi-apm` submodule, pinned to a released tag (currently `v0.10.0`)

The submodule gives development and CI a reproducible pin. To serve the skills you
installed with APM instead, point the server at them:

```bash
LINKED_ARCHI_SKILLS_DIR=~/.claude/skills linked-archi-mcp --data graph.trig
```

`doctor` reports which tree was used and each skill's version. To move the pin:

```bash
git -C vendor/linked-archi-apm fetch --tags && git -C vendor/linked-archi-apm checkout vX.Y.Z
```

## Run

### stdio (local client)

```bash
linked-archi-mcp --data dist/merged.trig --profile linked-archi-default
```

Claude Code:

```bash
claude mcp add linked-archi -- linked-archi-mcp --data /abs/path/merged.trig
```

Or in any client's JSON config:

```json
{
  "mcpServers": {
    "linked-archi": {
      "command": "linked-archi-mcp",
      "args": ["--data", "/abs/path/merged.trig"]
    }
  }
}
```

Over stdio, tool calls may name other files or endpoints with `data` / `endpoint`, since
the client is your own agent.

### Streamable HTTP

```bash
linked-archi-mcp http --port 8000 --data /srv/graph/merged.trig
# -> http://127.0.0.1:8000/mcp
```

Over HTTP the server is locked down by default:

- **Only the configured target.** Tool calls can't name files, endpoints, shapes or
  profile paths, only bundled profile names. `--allow-client-paths` lifts this; use it
  only for a trusted client.
- **Token required off loopback.** Binding a non-loopback address requires
  `$LINKED_ARCHI_MCP_TOKEN`, which clients send as `Authorization: Bearer <token>`. Behind
  a gateway that already authenticates, pass `--insecure-no-auth` instead.
- **DNS-rebinding protection.** On by default for loopback binds. Add `--allowed-host`
  for the public host name when serving behind a proxy.

```bash
LINKED_ARCHI_MCP_TOKEN=$(openssl rand -hex 32) \
  linked-archi-mcp http --host 0.0.0.0 --allowed-host graph-mcp.example.org \
  --endpoint https://graph.example.org/sparql --profile linked-archi-merged
```

### Options

| Flag | Meaning |
|---|---|
| `--data FILE` (repeatable) / `$LINKED_ARCHI_DATA` | Local RDF; files merge into one dataset |
| `--endpoint URL` | HTTPS SPARQL endpoint instead |
| `--profile` | Default profile (`linked-archi-default` if omitted) |
| `--catalog PATH` (repeatable) | Trusted project query catalogue |
| `--shapes FILE` (repeatable) | Default SHACL shapes for `validate_run` |
| `--store` | `memory` / `cached` / `readonly` / `refresh` (local store mode) |
| `--timeout-ms` | Endpoint request deadline |
| `--max-rows` | Rows returned per result by default (200) |
| `--run-timeout` | Deadline for one skill invocation, seconds (120) |

## Develop

```bash
git submodule update --init
pip install -e '.[test]'
pytest
```

The tests drive the server in-process with the MCP client against the committed fixtures
of the pinned skills.

## License

Apache-2.0
