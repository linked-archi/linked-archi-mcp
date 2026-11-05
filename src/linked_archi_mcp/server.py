"""The MCP server: linked-archi-apm skill owners exposed as tools, resources and a prompt.

Three outcomes come back from every query-shaped tool, and they are kept apart on purpose:

``answered``
    The owner ran. ``row_count == 0`` is a *finding*, not a failure.
``refused``
    The owner declined - the profile cannot support the template, or the SPARQL is not
    read-only. This is a routing answer naming why and what to ask instead. It is
    returned as a normal result, never as an empty one, because an empty result reads as
    "nothing exists".
error
    The owner could not run (bad arguments, unreadable data). Raised as a tool error.
"""

from __future__ import annotations

import atexit
import importlib.util
import json
import re
from typing import Annotated, Any, Literal

import anyio
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.mcpserver.resources import FileResource
from mcp_types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field, model_validator

from . import __version__
from .config import Config, ConfigError
from .evidence import EvidenceError, EvidenceStore
from .runner import Completed, Runner, RunTimeout
from .skills import Skills

READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False,
                            idempotent_hint=True, open_world_hint=False)
#: Reads a dataset that may be a remote endpoint.
READ_ONLY_OPEN = ToolAnnotations(read_only_hint=True, destructive_hint=False,
                                 idempotent_hint=True, open_world_hint=True)

DEFAULT_PROFILE = "linked-archi-default"

Scalar = str | int | float | bool


class Claim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim: str
    steps: list[int] = Field(default_factory=list, description="Step numbers the claim rests on.")


class Findings(BaseModel):
    """The evidence model's claim classes. An empty class is a statement; a missing one
    is an omission, so all five are required."""

    model_config = ConfigDict(extra="forbid")

    answer: str = ""
    graph_facts: list[Claim] = Field(description="Explicit triples or result bindings.")
    derived_facts: list[Claim] = Field(description="Produced by traversal or aggregation.")
    document_statements: list[Claim] = Field(description="Claims in a retrieved source.")
    inferences: list[Claim] = Field(description="Your conclusions from the above.")
    unknowns: list[Claim] = Field(description="Absent or ambiguous evidence; may cite no step.")


class BatchQuery(BaseModel):
    """One batch entry: exactly one of ``template`` or ``sparql``."""

    model_config = ConfigDict(extra="forbid")

    id: str | None = Field(None, description="Label echoed back on the result, e.g. step-01.")
    template: str | None = Field(None, description="Template name.")
    parameters: dict[str, Scalar] | None = Field(None, description="Template parameters (a plan manifest's `set`).")
    sparql: str | None = Field(None, description="Read-only SPARQL instead of a template.")

    @model_validator(mode="after")
    def _one_source(self) -> "BatchQuery":
        if bool(self.template) == bool(self.sparql):
            raise ValueError("give exactly one of template or sparql")
        if self.sparql and self.parameters:
            raise ValueError("parameters apply to templates only")
        return self

Stage = Literal["orientation", "resolution", "discovery", "analysis", "enrichment",
                "quality", "views"]

INSTRUCTIONS = """\
Read-only analysis of architecture knowledge graphs built by the Linked.Archi converters
(ArchiMate, BPMN, C4/Structurizr, Backstage, LeanIX as RDF).

Workflow:
1. dataset_connect, then profile_recommend and profile_verify: the profile tells every
   query what this dataset calls things. A profile that does not fit fails silently.
2. catalog_list (with the profile) to see which tested templates this dataset supports.
3. query_run a template; query_literal only when no template fits.
4. For a multi-step question, analyse_plan returns ordered steps. Run each step's
   `template` with its `parameters` through query_run. Run a plan batch whose status is
   "ready" through query_batch instead (its manifest's `set` is `parameters`): the
   dataset loads once rather than once per query.
5. Every answered query carries an evidence_id. analyse_bundle assembles the ones an
   answer rests on, with findings split by claim class, into a reproducible bundle.

Rules:
- status "refused" is an answer, not an error: read `message` and use the named
  alternative. Never report a refused template as "no results".
- row_count 0 on an answered query is a finding. State what it does and does not prove
  (catalog_show gives each template's `does_not_prove`).
- Separate graph facts from inference from unknowns. Cite template and query_id.
The skills' own documentation is available as resources under linked-archi://skills/.
"""


def build_server(config: Config, skills: Skills, *, name: str = "linked-archi",
                 evidence: EvidenceStore | None = None) -> MCPServer:
    runner = Runner(skills, timeout_s=config.run_timeout_s)
    if evidence is None:
        evidence = EvidenceStore()
        atexit.register(evidence.close)
    mcp = MCPServer(name=name, title="Linked.Archi knowledge graph",
                    version=__version__, instructions=INSTRUCTIONS)

    async def run(owner: str, args: list[str], *, stdin: str | None = None) -> Completed:
        try:
            return await runner.run(owner, args, stdin=stdin)
        except RunTimeout as exc:
            raise ToolError(str(exc)) from exc

    def guard(fn):
        """Turn configuration refusals into tool errors with their message intact."""
        def wrapped(*a, **kw):
            try:
                return fn(*a, **kw)
            except ConfigError as exc:
                raise ToolError(str(exc)) from exc
        return wrapped

    target_for = guard(config.target_for)
    profile_for = guard(config.profile_for)
    paths_for = guard(config.paths_for)

    def profile_args(profile: str | None) -> list[str]:
        resolved = profile_for(profile)
        return ["--profile", resolved] if resolved else []

    # -- catalogue --------------------------------------------------------------------

    async def dump(profile: str | None, extra: list[str]) -> dict[str, Any]:
        # Always gate: an ungated listing has no availability, and a client reading it
        # would pick templates the dataset's profile is about to refuse.
        gated = profile_for(profile) or DEFAULT_PROFILE
        done = await run("query", ["catalog", "dump", "--profile", gated,
                                   *config.query_args(), *extra])
        return _json_or_error(done)

    @mcp.tool(annotations=READ_ONLY)
    async def catalog_list(
        profile: Annotated[str | None, Field(description="Profile to gate availability against. Omit for the server default.")] = None,
        stage: Annotated[list[Stage] | None, Field(description="Only these catalogue stages.")] = None,
        notation: Annotated[list[str] | None, Field(description="Only these notations, e.g. archimate, bpmn, c4.")] = None,
        concern: Annotated[list[str] | None, Field(description="Only model questions informing this concern (IRI, label or local name).")] = None,
    ) -> dict[str, Any]:
        """List the tested query templates, each marked available or refused for the profile.

        Refused entries carry the unmet requirement and an alternative. Use catalog_show
        for one template's parameters and what its answer does not prove."""
        extra: list[str] = []
        for value in stage or []:
            extra += ["--stage", value]
        for value in notation or []:
            extra += ["--notation", value]
        for value in concern or []:
            extra += ["--concern", value]
        data = await dump(profile, extra)
        templates = []
        for tname, entry in data.get("templates", {}).items():
            templates.append({
                "template": tname,
                "stage": entry.get("stage"),
                "notation": entry.get("notation"),
                "purpose": entry.get("purpose"),
                "available": entry.get("available"),
                "caveat": entry.get("caveat") or None,
                "unmet": entry.get("unmet") or [],
                "alternatives": entry.get("alternatives") or [],
                "parameters": sorted((entry.get("parameters") or {}).keys()),
            })
        return {"profile": data.get("profile"), "count": len(templates), "templates": templates}

    @mcp.tool(annotations=READ_ONLY)
    async def catalog_show(
        template: Annotated[str, Field(description="Template name, e.g. core/dependents-qualified.")],
        profile: Annotated[str | None, Field(description="Profile to gate against. Omit for the server default.")] = None,
    ) -> dict[str, Any]:
        """One template's full metadata: purpose, parameters with types and bounds, what it
        answers, what it does NOT prove, requirements, and availability for the profile."""
        data = await dump(profile, ["--template", template])
        entry = data.get("templates", {}).get(template)
        if entry is None:
            raise ToolError(f"No template {template!r}. Use catalog_list to see what exists.")
        return {"template": template, "profile": data.get("profile"), **entry}

    # -- queries ----------------------------------------------------------------------

    def set_args(parameters: dict[str, str | int | float | bool] | None) -> list[str]:
        out: list[str] = []
        for key, value in (parameters or {}).items():
            out += ["--set", f"{key}={_scalar(value)}"]
        return out

    @mcp.tool(annotations=READ_ONLY)
    async def query_render(
        template: Annotated[str, Field(description="Template name, e.g. core/resolve-element.")],
        parameters: Annotated[dict[str, str | int | float | bool] | None, Field(description="Template parameters by name; see catalog_show.")] = None,
        profile: Annotated[str | None, Field(description="Profile to render against. Omit for the server default.")] = None,
    ) -> dict[str, Any]:
        """Render a template to SPARQL without executing it."""
        done = await run("query", ["query", "render", template, *profile_args(profile),
                                   *config.query_args(), *set_args(parameters)])
        if done.returncode == 0:
            return {"status": "rendered", "template": template, "sparql": done.stdout}
        return _refusal_or_error(done)

    @mcp.tool(annotations=READ_ONLY_OPEN)
    async def query_run(
        template: Annotated[str, Field(description="Template name, e.g. core/dependents-qualified.")],
        parameters: Annotated[dict[str, str | int | float | bool] | None, Field(description="Template parameters by name; see catalog_show. The query's own row cap is LIMIT.")] = None,
        profile: Annotated[str | None, Field(description="Profile. Omit for the server default.")] = None,
        data: Annotated[list[str] | None, Field(description="Local RDF files to query instead of the server's dataset.")] = None,
        endpoint: Annotated[str | None, Field(description="HTTPS SPARQL endpoint instead of the server's dataset.")] = None,
        max_rows: Annotated[int | None, Field(ge=1, description="Rows to return in this response; row_count is always the full count.")] = None,
        include_query: Annotated[bool, Field(description="Include the executed SPARQL text.")] = False,
    ) -> dict[str, Any]:
        """Execute a catalogued template, read-only, and return the result envelope with
        provenance (query_id, dataset_id, profile, executed_at) and any warnings.

        status "refused" means this profile cannot support the template: use an
        alternative named in `message`. An answered query with row_count 0 is a finding."""
        target = target_for(data, endpoint)
        done = await run("query", ["query", "run", template, "--json",
                                   *profile_args(profile), *config.query_args(),
                                   *config.connect_args(target), *set_args(parameters)])
        return _envelope(done, evidence, template, max_rows or config.max_rows, include_query)

    @mcp.tool(annotations=READ_ONLY_OPEN)
    async def query_literal(
        sparql: Annotated[str, Field(description="A read-only SPARQL query. Profile directives are expanded.")],
        profile: Annotated[str | None, Field(description="Profile. Omit for the server default.")] = None,
        data: Annotated[list[str] | None, Field(description="Local RDF files instead of the server's dataset.")] = None,
        endpoint: Annotated[str | None, Field(description="HTTPS SPARQL endpoint instead of the server's dataset.")] = None,
        max_rows: Annotated[int | None, Field(ge=1, description="Rows to return in this response.")] = None,
    ) -> dict[str, Any]:
        """Execute a hand-written read-only SPARQL query. Prefer a template: they encode the
        graph's conventions (named graphs, qualified relationships) a hand-written query
        silently misses. Updates, LOAD and SERVICE are refused before anything runs."""
        target = target_for(data, endpoint)
        done = await run("query", ["query", "literal", "--json", "--query", sparql,
                                   *profile_args(profile), *config.connect_args(target)])
        return _envelope(done, evidence, "literal", max_rows or config.max_rows,
                         include_query=False)

    @mcp.tool(annotations=READ_ONLY_OPEN)
    async def query_batch(
        queries: Annotated[list[BatchQuery], Field(min_length=1, max_length=50, description="Independent queries, each a template (with parameters) or read-only sparql.")],
        profile: Annotated[str | None, Field(description="Profile for every entry. Omit for the server default.")] = None,
        data: Annotated[list[str] | None, Field(description="Local RDF files instead of the server's dataset.")] = None,
        endpoint: Annotated[str | None, Field(description="HTTPS SPARQL endpoint instead of the server's dataset.")] = None,
        max_rows: Annotated[int | None, Field(ge=1, description="Rows to return per result.")] = None,
    ) -> dict[str, Any]:
        """Run several independent queries in one invocation, loading the dataset once.

        Use it for queries whose parameters are already known - an analyse_plan batch
        whose status is "ready", or several lookups - not for steps that need an earlier
        result. Every entry is rendered and checked read-only before any runs, so one
        refused entry refuses the whole batch: drop or replace it and resend."""
        target = target_for(data, endpoint)
        cap = max_rows or config.max_rows
        with evidence.scratch() as scratch:
            entries, reserved = [], []
            for index, query in enumerate(queries, start=1):
                label = query.id or query.template or f"query-{index}"
                evidence_id, out = evidence.new_path(label)
                reserved.append((label, query, evidence_id, out))
                entry: dict[str, Any] = {"id": label, "out": str(out)}
                if query.template:
                    entry["template"] = query.template
                    if query.parameters:
                        entry["set"] = {k: _scalar(v) for k, v in query.parameters.items()}
                else:
                    entry["query"] = query.sparql
                entries.append(entry)
            manifest = scratch / "batch.json"
            manifest.write_text(json.dumps({"schema_version": 1, "queries": entries}),
                                encoding="utf-8")
            done = await run("query", ["query", "batch", str(manifest),
                                       *profile_args(profile), *config.query_args(),
                                       *config.connect_args(target),
                                       "-o", str(scratch / "summary.txt")])
        if done.returncode != 0:
            for _, _, _, out in reserved:
                out.unlink(missing_ok=True)
            return _refusal_or_error(done)
        results = []
        for label, query, evidence_id, out in reserved:
            try:
                envelope = json.loads(out.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ToolError(f"Batch entry {label!r} wrote no readable envelope: {exc}") from exc
            evidence.commit(evidence_id, out)
            results.append({"id": label,
                            **_shape(envelope, evidence_id, cap, include_query=False)})
        return {"status": "answered", "count": len(results), "results": results}

    @mcp.tool(annotations=READ_ONLY)
    async def query_lint(
        sparql: Annotated[str, Field(description="SPARQL to check.")],
    ) -> dict[str, Any]:
        """Decide whether a SPARQL query is read-only, without running it."""
        request = json.dumps({"schema_version": 1, "query": sparql})
        done = await run("query", ["_machine", "lint"], stdin=request)
        return _json_or_error(done, ok=(0, 1))

    # -- datasets and profiles --------------------------------------------------------

    @mcp.tool(annotations=READ_ONLY_OPEN)
    async def dataset_connect(
        data: Annotated[list[str] | None, Field(description="Local RDF files (.trig/.nq keep named graphs).")] = None,
        endpoint: Annotated[str | None, Field(description="HTTPS SPARQL endpoint.")] = None,
    ) -> dict[str, Any]:
        """Load the dataset and report what actually loaded: quads, named graphs, and any
        inputs that lost graph identity. Run this first."""
        target = target_for(data, endpoint)
        done = await run("connect", ["connect", *config.connect_args(target)])
        return _text_or_error(done, target=target.describe())

    if config.allow_client_paths:
        @mcp.tool(annotations=READ_ONLY)
        async def dataset_candidates(
            directory: Annotated[str | None, Field(description="Directory to look in. Omit for the server's working directory and git root.")] = None,
        ) -> dict[str, Any]:
            """List candidate RDF files near a directory. Chooses none of them: pick
            explicitly and pass them as `data`."""
            done = await run("connect", ["datasets", *([directory] if directory else [])])
            return _text_or_error(done)

    @mcp.tool(annotations=READ_ONLY)
    async def profile_list() -> dict[str, Any]:
        """The bundled graph profiles: converter output shapes and worked examples."""
        done = await run("profile", ["list"])
        if done.returncode != 0:
            raise ToolError(_message(done))
        names = [line.split()[0] for line in done.stdout.splitlines() if line.strip()]
        profiles: list[dict[str, Any]] = [{} for _ in names]

        async def one(i: int, pname: str) -> None:
            resolved = await _resolve_profile(pname)
            profiles[i] = {"name": pname, "version": resolved.get("version"),
                           "description": resolved.get("description"),
                           "capabilities": resolved.get("capabilities")}

        async with anyio.create_task_group() as tg:
            for i, pname in enumerate(names):
                tg.start_soon(one, i, pname)
        return {"default": config.profile or DEFAULT_PROFILE, "profiles": profiles}

    async def _resolve_profile(pname: str | None) -> dict[str, Any]:
        request = {"schema_version": 1}
        if pname:
            request["profile"] = pname
        done = await run("profile", ["_machine", "resolve"], stdin=json.dumps(request))
        return _json_or_error(done)

    @mcp.tool(annotations=READ_ONLY)
    async def profile_show(
        profile: Annotated[str | None, Field(description="Profile name. Omit for the server default.")] = None,
    ) -> dict[str, Any]:
        """The fully resolved profile: namespaces, graph layout, role bindings,
        capabilities, notations, taxonomies and limits."""
        return await _resolve_profile(profile_for(profile))

    @mcp.tool(annotations=READ_ONLY_OPEN)
    async def profile_recommend(
        data: Annotated[list[str] | None, Field(description="Local RDF files instead of the server's dataset.")] = None,
        endpoint: Annotated[str | None, Field(description="HTTPS SPARQL endpoint instead of the server's dataset.")] = None,
    ) -> dict[str, Any]:
        """Probe the dataset's shape and name the starting profile that fits it."""
        target = target_for(data, endpoint)
        done = await run("profile", ["recommend", *config.connect_args(target, store=False)])
        return _text_or_error(done, target=target.describe())

    @mcp.tool(annotations=READ_ONLY_OPEN)
    async def profile_verify(
        profile: Annotated[str | None, Field(description="Profile to check. Omit for the server default.")] = None,
        data: Annotated[list[str] | None, Field(description="Local RDF files instead of the server's dataset.")] = None,
        endpoint: Annotated[str | None, Field(description="HTTPS SPARQL endpoint instead of the server's dataset.")] = None,
        show_all: Annotated[bool, Field(description="Report confirmed checks too, not only warnings and errors.")] = False,
    ) -> dict[str, Any]:
        """Check a profile's claims against the dataset. status "drift" means a claim is
        false about this dataset: fix the profile or choose another before querying."""
        target = target_for(data, endpoint)
        args = ["verify", *profile_args(profile), *config.connect_args(target, store=False)]
        if show_all:
            args.append("--all")
        done = await run("profile", args)
        if done.returncode not in (0, 1):
            raise ToolError(_message(done))
        return {"status": "verified" if done.returncode == 0 else "drift",
                "target": target.describe(), "report": done.stdout,
                "diagnostics": done.stderr or None}

    # -- analysis ---------------------------------------------------------------------

    @mcp.tool(annotations=READ_ONLY)
    async def analyse_patterns() -> dict[str, Any]:
        """The analysis patterns a question can be routed to, and what triggers each."""
        done = await run("analyse", ["plan", "--list-patterns"])
        return _text_or_error(done)

    @mcp.tool(annotations=READ_ONLY)
    async def analyse_plan(
        question: Annotated[str | None, Field(description="The question. Quote names you mean, e.g. what depends on \"Order Service\"?")] = None,
        mode: Annotated[str | None, Field(description="Name a pattern instead of routing (see analyse_patterns).")] = None,
        concerns: Annotated[list[str] | None, Field(description="Plan the model questions informing these stakeholder concerns instead of a question.")] = None,
        budget: Annotated[int, Field(ge=1, le=50, description="Query budget to plan within.")] = 12,
        profile: Annotated[str | None, Field(description="Profile. Omit for the server default.")] = None,
        definitions: Annotated[bool, Field(description="Include term definitions.")] = False,
    ) -> dict[str, Any]:
        """Route a question to an analysis pattern and return ordered steps. Executes
        nothing: run each step's `template` with its `parameters` via query_run, honour
        each step's `stop_when`, and resolve placeholders from earlier results. A batch
        with status "ready" can go through query_batch in one call; one "awaiting-evidence"
        waits until the steps it depends on have been reviewed."""
        if not question and not concerns:
            raise ToolError("Give a question, or concerns to plan for.")
        request: dict[str, Any] = {"schema_version": 1, "budget": budget,
                                   "definitions": definitions}
        if question:
            request["question"] = question
        if concerns:
            request["concerns"] = concerns
        if mode:
            request["mode"] = mode
        # Always name one: without it every step's availability is "unknown", and the
        # planner then batches nothing and cannot flag a step this dataset will refuse.
        request["profile"] = profile_for(profile) or DEFAULT_PROFILE
        if not config.target.empty:
            request.update(config.target.describe())
        done = await run("analyse", ["_machine", "plan"], stdin=json.dumps(request))
        if done.returncode == 1:
            return _refusal(done)
        return _json_or_error(done)

    @mcp.tool(annotations=READ_ONLY)
    async def analyse_bundle(
        evidence_ids: Annotated[list[str], Field(min_length=1, description="evidence_id of each query result, in step order. Step N in findings is the Nth id here.")],
        question: Annotated[str, Field(description="The question the investigation answers.")],
        findings: Annotated[Findings | None, Field(description="The interpretation, split by claim class. Every class must be present, even empty; every claim except an unknown cites step numbers.")] = None,
        dataset_revision: Annotated[str | None, Field(description="Dataset revision (e.g. a git commit) to record. Recorded as caller-supplied.")] = None,
    ) -> dict[str, Any]:
        """Assemble query results into one reviewable, reproducible investigation bundle,
        and render it as Markdown.

        Refused (status "refused") when results span two datasets or two profiles, when a
        claim class is missing, or when a claim cites no step or a step not in the bundle."""
        try:
            steps = [str(evidence.path(e)) for e in evidence_ids]
        except EvidenceError as exc:
            raise ToolError(str(exc.args[0])) from exc
        with evidence.scratch() as scratch:
            request: dict[str, Any] = {"schema_version": 1, "steps": steps,
                                       "question": question}
            if findings is not None:
                findings_file = scratch / "findings.json"
                findings_file.write_text(findings.model_dump_json(), encoding="utf-8")
                request["findings"] = str(findings_file)
            if dataset_revision:
                request["dataset_revision"] = dataset_revision
            done = await run("analyse", ["_machine", "bundle"], stdin=json.dumps(request))
            if done.returncode == 1:
                return _refusal(done)
            bundle = _json_or_error(done)
            bundle_file = scratch / "bundle.json"
            bundle_file.write_text(done.stdout, encoding="utf-8")
            rendered = await run("analyse", ["render", "--bundle", str(bundle_file)])
        if rendered.returncode != 0:
            raise ToolError(_message(rendered))
        return {"status": "bundled", "bundle": bundle, "markdown": rendered.stdout}

    # -- validation -------------------------------------------------------------------

    if skills.has("validate"):
        @mcp.tool(annotations=READ_ONLY)
        async def validate_run(
            shapes: Annotated[list[str] | None, Field(description="SHACL shapes files. Omit for the server's configured shapes.")] = None,
            data: Annotated[list[str] | None, Field(description="RDF files to validate instead of the server's dataset.")] = None,
            ontology: Annotated[list[str] | None, Field(description="Ontology files loaded for RDFS reasoning.")] = None,
            rdfs_reasoning: Annotated[bool, Field(description="Apply rdfs:subClassOf reasoning.")] = True,
        ) -> dict[str, Any]:
            """Run SHACL in-process over local RDF and report conformance with target-class
            coverage. A run that matched no target class is reported as such rather than
            as a pass."""
            shape_files = paths_for("shapes", shapes, config.shapes)
            if not shape_files:
                raise ToolError("No SHACL shapes. Pass `shapes`, or start the server with --shapes.")
            target = target_for(data, None)
            if not target.data:
                raise ToolError("SHACL validation needs local RDF files, not an endpoint.")
            args = ["run", "--json"]
            for path in target.data:
                args += ["--data", path]
            for path in shape_files:
                args += ["--shapes", path]
            for path in paths_for("ontology", ontology, ()):
                args += ["--ontology", path]
            if not rdfs_reasoning:
                args.append("--no-rdfs-reasoning")
            done = await run("validate", args)
            result = _json_or_error(done, ok=(0, 1))
            return {"status": "conforms" if done.returncode == 0 else "results", **result}

    # -- diagnostics ------------------------------------------------------------------

    @mcp.tool(annotations=READ_ONLY)
    async def doctor() -> dict[str, Any]:
        """Where the skills were found, their versions, optional dependencies, and the
        server's configured dataset and profile."""
        from .skills import OWNERS
        return {
            "server_version": __version__,
            "skills_root": str(skills.root),
            "skills_origin": skills.origin,
            "skills": {owner: {"installed": skills.has(owner), "version": skills.version(owner)}
                       for owner in OWNERS},
            "python_packages": {pkg: importlib.util.find_spec(pkg) is not None
                                for pkg in ("yaml", "pyoxigraph", "pyshacl")},
            "target": config.target.describe(),
            "profile": config.profile or DEFAULT_PROFILE,
            "catalogs": list(config.catalogs),
            "accepts_client_paths": config.allow_client_paths,
        }

    # -- resources --------------------------------------------------------------------

    for doc in skills.documents():
        mcp.add_resource(FileResource(
            uri=doc.uri,
            name=f"{doc.skill}/{doc.relpath}",
            title=f"{doc.skill}: {doc.relpath}",
            description=_doc_description(doc.relpath, doc.skill),
            mime_type="text/markdown",
            path=doc.path,
        ))

    # -- prompts ----------------------------------------------------------------------

    @mcp.prompt(title="Investigate an architecture question")
    def investigate(question: str) -> str:
        """Plan and run an evidence-grounded investigation of one question."""
        return (
            f"Investigate this architecture question against the connected graph:\n\n"
            f"{question}\n\n"
            "1. If not done this session: dataset_connect, profile_recommend, profile_verify.\n"
            "2. analyse_plan with the question. Run ready batches through query_batch and the\n"
            "   remaining steps through query_run in order, binding placeholders from earlier\n"
            "   results; stop where a step's stop_when holds.\n"
            "3. A refused step is a routing answer: take the named alternative.\n"
            "4. Answer in three parts - graph facts (cite template and query_id), inference,\n"
            "   and unknowns - and say what the evidence cannot establish.\n"
            "5. analyse_bundle the evidence_ids your answer rests on, with those findings, so\n"
            "   the answer can be reviewed and reproduced."
        )

    return mcp


# -- result shaping -----------------------------------------------------------------------


def _message(done: Completed) -> str:
    text = (done.stderr.strip() or done.stdout.strip()
            or f"{done.owner} exited {done.returncode} with no output.")
    return text


def _json_or_error(done: Completed, ok: tuple[int, ...] = (0,)) -> dict[str, Any]:
    if done.returncode not in ok:
        raise ToolError(_message(done))
    try:
        parsed = json.loads(done.stdout)
    except json.JSONDecodeError as exc:
        raise ToolError(f"{done.owner} returned output that is not JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ToolError(f"{done.owner} returned JSON that is not an object.")
    return parsed


def _text_or_error(done: Completed, **extra: Any) -> dict[str, Any]:
    if done.returncode != 0:
        raise ToolError(_message(done))
    return {**extra, "report": done.stdout, "diagnostics": done.stderr or None}


_TRY_INSTEAD = re.compile(r"^Try instead:\s*(.+?)(?:\s*\(.*\))?\.?$", re.MULTILINE)


def _refusal(done: Completed) -> dict[str, Any]:
    message = _message(done)
    alternatives: list[str] = []
    match = _TRY_INSTEAD.search(message)
    if match:
        alternatives = [a.strip() for a in match.group(1).split(",") if a.strip()]
    return {"status": "refused", "message": message, "alternatives": alternatives}


def _refusal_or_error(done: Completed) -> dict[str, Any]:
    if done.returncode == 1:
        return _refusal(done)
    raise ToolError(_message(done))


def _scalar(value: str | int | float | bool) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _envelope(done: Completed, evidence: EvidenceStore, label: str, max_rows: int,
              include_query: bool) -> dict[str, Any]:
    if done.returncode != 0:
        return _refusal_or_error(done)
    envelope = _json_or_error(done)
    # Saved byte for byte as the query owner printed it: that is the bundle's input.
    evidence_id = evidence.save(label, done.stdout)
    return _shape(envelope, evidence_id, max_rows, include_query)


def _shape(envelope: dict[str, Any], evidence_id: str, max_rows: int,
           include_query: bool) -> dict[str, Any]:
    envelope = dict(envelope)
    rows = envelope.get("rows")
    if isinstance(rows, list) and len(rows) > max_rows:
        envelope["rows"] = rows[:max_rows]
        envelope["rows_omitted"] = len(rows) - max_rows
    if not include_query:
        envelope.pop("query", None)
    return {"status": "answered", "evidence_id": evidence_id, **envelope}


def _doc_description(relpath: str, skill: str) -> str:
    if relpath == "SKILL.md":
        return f"How and when to use {skill}: the skill's own instructions."
    return f"{skill} reference: {relpath.removeprefix('references/')}"
