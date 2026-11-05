"""End to end: the MCP client drives the server in-process against the skills' fixtures."""

from __future__ import annotations

import json
from typing import Any

import pytest
from mcp import Client

from linked_archi_mcp.config import Config, Target
from linked_archi_mcp.evidence import EvidenceStore
from linked_archi_mcp.server import build_server

from .conftest import BASE

pytestmark = pytest.mark.anyio

SHAPES = """\
@prefix sh: <http://www.w3.org/ns/shacl#> .
@prefix ex: <https://example.org/> .
ex:ThingShape a sh:NodeShape ;
  sh:targetClass ex:Thing ;
  sh:property [ sh:path ex:name ; sh:minCount 1 ] .
"""
CONFORMING = """\
@prefix ex: <https://example.org/> .
ex:a a ex:Thing ; ex:name "A" .
"""
VIOLATING = """\
@prefix ex: <https://example.org/> .
ex:a a ex:Thing .
"""


async def call(server, name: str, args: dict[str, Any] | None = None):
    async with Client(server) as client:
        return await client.call_tool(name, args or {})


@pytest.fixture
def evidence(tmp_path):
    return EvidenceStore(tmp_path / "evidence")


@pytest.fixture
def server(config, skills, evidence):
    return build_server(config, skills, evidence=evidence)


async def test_surface(server):
    async with Client(server) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
        assert {"query_run", "catalog_list", "profile_verify", "analyse_plan",
                "validate_run", "doctor"} <= set(tools)
        assert all(t.annotations and t.annotations.read_only_hint for t in tools.values())
        resources = (await client.list_resources()).resources
        safety = next(r for r in resources if str(r.uri).endswith("linked-archi-query/references/safety.md"))
        read = await client.read_resource(safety.uri)
        assert "SERVICE" in read.contents[0].text
        prompts = (await client.list_prompts()).prompts
        assert [p.name for p in prompts] == ["investigate"]


async def test_query_run_answers_with_provenance(server):
    result = await call(server, "query_run", {"template": "core/models"})
    assert not result.is_error
    out = result.structured_content
    assert out["status"] == "answered"
    assert out["row_count"] == 5 and len(out["rows"]) == 5
    assert out["template"] == "core/models" and out["query_id"] and out["dataset_id"]
    assert "query" not in out


async def test_rows_are_capped_but_counted(server):
    result = await call(server, "query_run", {"template": "core/models", "max_rows": 2,
                                              "include_query": True})
    out = result.structured_content
    assert out["row_count"] == 5 and len(out["rows"]) == 2 and out["rows_omitted"] == 3
    assert out["query"].lstrip().startswith("#")


async def test_refusal_is_an_answer_not_an_empty_result(server):
    result = await call(server, "query_run", {"template": "core/dependents-direct",
                                              "profile": "linked-archi-qualified"})
    assert not result.is_error
    out = result.structured_content
    assert out["status"] == "refused"
    assert "direct_rel_triples" in out["message"]
    assert out["alternatives"] == ["core/dependents-qualified", "core/neighbours-qualified"]
    assert "rows" not in out


async def test_unknown_template_is_an_error(server):
    result = await call(server, "query_run", {"template": "core/does-not-exist"})
    assert result.is_error
    assert "No template" in result.content[0].text


async def test_literal_update_is_refused(server):
    result = await call(server, "query_literal", {"sparql": "DELETE WHERE { ?s ?p ?o }"})
    assert result.structured_content["status"] == "refused"


async def test_literal_select_runs(server):
    result = await call(server, "query_literal", {
        "sparql": "SELECT (COUNT(*) AS ?n) WHERE { GRAPH ?g { ?s ?p ?o } }"})
    out = result.structured_content
    assert out["status"] == "answered" and int(out["rows"][0]["n"]) > 0


async def test_lint(server):
    ok = (await call(server, "query_lint", {"sparql": "SELECT * WHERE { ?s ?p ?o }"}))
    assert ok.structured_content["accepted"] is True
    bad = (await call(server, "query_lint", {"sparql": "SELECT * WHERE { SERVICE <https://x> { ?s ?p ?o } }"}))
    assert bad.structured_content["accepted"] is False


async def test_render_with_parameters(server):
    result = await call(server, "query_render", {"template": "core/resolve-element",
                                                 "parameters": {"TERM": "order service"}})
    out = result.structured_content
    assert out["status"] == "rendered" and "order service" in out["sparql"].lower()


async def test_catalog_is_always_gated(server):
    out = (await call(server, "catalog_list", {"stage": ["orientation"]})).structured_content
    assert out["profile"]["id"] == "linked-archi-default"
    assert all(t["available"] is not None for t in out["templates"])
    assert all(t["stage"] == "orientation" for t in out["templates"])
    gated = (await call(server, "catalog_list", {"profile": "linked-archi-qualified",
                                                 "stage": ["analysis"]})).structured_content
    direct = next(t for t in gated["templates"] if t["template"] == "core/dependents-direct")
    assert direct["available"] is False and direct["unmet"]


async def test_catalog_show(server):
    out = (await call(server, "catalog_show", {"template": "core/models"})).structured_content
    assert out["does_not_prove"] and "LIMIT" in out["parameters"]
    missing = await call(server, "catalog_show", {"template": "core/nope"})
    assert missing.is_error


async def test_connect_and_profiles(server):
    connected = (await call(server, "dataset_connect")).structured_content
    assert "named graph" in connected["report"]
    verified = (await call(server, "profile_verify")).structured_content
    assert verified["status"] == "verified"
    recommended = (await call(server, "profile_recommend")).structured_content
    assert "direct rel triples" in recommended["report"]
    shown = (await call(server, "profile_show", {"profile": "examples/curated-store"})).structured_content
    assert shown["name"] == "curated-store" and "capabilities" in shown
    listed = (await call(server, "profile_list")).structured_content
    names = [p["name"] for p in listed["profiles"]]
    assert "linked-archi-default" in names and all(p["version"] for p in listed["profiles"])


async def test_profile_drift_is_a_finding(server):
    # A flattened-store profile claims no named graphs; this dataset has seventeen.
    out = (await call(server, "profile_verify", {"profile": "examples/flattened-turtle"})).structured_content
    assert out["status"] == "drift"
    assert "error" in out["report"]


async def test_plan_records_the_server_target(server):
    result = await call(server, "analyse_plan", {"question": 'what depends on "Order Service"?'})
    out = result.structured_content
    assert out["pattern"] == "impact-and-dependency"
    assert out["data"] == [str(BASE)]
    assert out["steps"] and all("template" in s for s in out["steps"])


async def test_plan_needs_a_question(server):
    assert (await call(server, "analyse_plan", {})).is_error


async def test_validate(server, tmp_rdf):
    shapes = tmp_rdf("shapes.ttl", SHAPES)
    good = (await call(server, "validate_run", {
        "shapes": [shapes], "data": [tmp_rdf("good.ttl", CONFORMING)]})).structured_content
    assert good["status"] == "conforms"
    bad = await call(server, "validate_run", {
        "shapes": [shapes], "data": [tmp_rdf("bad.ttl", VIOLATING)]})
    assert not bad.is_error and bad.structured_content["status"] == "results"


async def test_doctor(server):
    out = (await call(server, "doctor")).structured_content
    assert out["skills_origin"] == "vendored submodule"
    assert out["skills"]["query"]["version"]
    assert out["accepts_client_paths"] is True


async def test_locked_server_refuses_client_paths(skills, tmp_rdf):
    locked = build_server(Config(target=Target((str(BASE),)), allow_client_paths=False), skills)
    async with Client(locked) as client:
        names = {t.name for t in (await client.list_tools()).tools}
        assert "dataset_candidates" not in names
        result = await client.call_tool("query_run", {"template": "core/models",
                                                      "data": ["/etc/hostname"]})
        assert result.is_error and "does not accept" in result.content[0].text
        result = await client.call_tool("query_run", {"template": "core/models",
                                                      "profile": tmp_rdf("p.yaml", "x: 1")})
        assert result.is_error and "is a path" in result.content[0].text
        ok = await client.call_tool("query_run", {"template": "core/models"})
        assert ok.structured_content["status"] == "answered"


async def test_no_dataset_is_explained(skills):
    bare = build_server(Config(), skills)
    result = await call(bare, "query_run", {"template": "core/models"})
    assert result.is_error and "No dataset" in result.content[0].text


async def test_stdio_transport():
    import sys

    from mcp import StdioServerParameters

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "linked_archi_mcp", "--data", str(BASE)],
    )
    async with Client(params) as client:
        result = await client.call_tool("query_run", {"template": "core/models"})
        assert result.structured_content["status"] == "answered"


# -- batching and evidence ----------------------------------------------------------------


async def test_answers_carry_saved_evidence(server, evidence):
    out = (await call(server, "query_run", {"template": "core/models", "max_rows": 1})).structured_content
    saved = json.loads(evidence.path(out["evidence_id"]).read_text())
    # The saved envelope is the owner's, untouched by the response shaping.
    assert len(saved["rows"]) == 5 and "query" in saved
    assert saved["query_id"] == out["query_id"]


async def test_batch_loads_once(server, evidence):
    result = await call(server, "query_batch", {"queries": [
        {"id": "inventory", "template": "core/inventory-summary"},
        {"template": "core/resolve-element", "parameters": {"TERM": "order"}},
        {"sparql": "SELECT (COUNT(*) AS ?n) WHERE { GRAPH ?g { ?s ?p ?o } }"},
    ]})
    assert not result.is_error, result.content
    out = result.structured_content
    assert out["status"] == "answered" and out["count"] == 3
    ids = [r["id"] for r in out["results"]]
    assert ids == ["inventory", "core/resolve-element", "query-3"]
    assert out["results"][0]["load_ms"] >= 0
    assert all(r["load_ms"] == 0 for r in out["results"][1:])
    for r in out["results"]:
        assert r["status"] == "answered" and evidence.path(r["evidence_id"]).is_file()


async def test_batch_with_one_refused_entry_refuses_and_leaves_nothing(server, evidence):
    result = await call(server, "query_batch", {"profile": "linked-archi-qualified", "queries": [
        {"template": "core/models"}, {"template": "core/dependents-direct"}]})
    out = result.structured_content
    assert out["status"] == "refused"
    assert "core/dependents-qualified" in out["alternatives"]
    assert not list(evidence.root.glob("e-*.json"))


async def test_batch_entry_needs_exactly_one_source(server):
    both = await call(server, "query_batch", {"queries": [
        {"template": "core/models", "sparql": "SELECT * WHERE { ?s ?p ?o }"}]})
    assert both.is_error
    neither = await call(server, "query_batch", {"queries": [{"id": "x"}]})
    assert neither.is_error


async def test_plan_ready_batch_runs_through_query_batch(server):
    plan = (await call(server, "analyse_plan", {
        "question": 'what depends on "Order Service"?'})).structured_content
    ready = [b for b in plan["batches"] if b["status"] == "ready"]
    assert ready, plan["batches"]
    queries = [{"id": q["id"], "template": q["template"], "parameters": q.get("set") or None}
               for q in ready[0]["manifest"]["queries"]]
    out = (await call(server, "query_batch", {"queries": queries})).structured_content
    assert out["status"] == "answered" and out["count"] == len(queries)


FINDINGS = {
    "answer": "Five models are loaded.",
    "graph_facts": [{"claim": "Five models are present.", "steps": [2]}],
    "derived_facts": [],
    "document_statements": [],
    "inferences": [],
    "unknowns": [{"claim": "Whether the models are current."}],
}


async def test_bundle_round_trip(server):
    batch = (await call(server, "query_batch", {"queries": [
        {"template": "core/inventory-summary"}, {"template": "core/models"}]})).structured_content
    ids = [r["evidence_id"] for r in batch["results"]]
    result = await call(server, "analyse_bundle", {
        "evidence_ids": ids, "question": "What is loaded?", "findings": FINDINGS})
    assert not result.is_error, result.content
    out = result.structured_content
    assert out["status"] == "bundled"
    assert [s["number"] for s in out["bundle"]["steps"]] == [1, 2]
    assert "What is loaded?" in out["markdown"] and "Five models are present." in out["markdown"]


async def test_bundle_refuses_an_uncited_claim(server):
    run = (await call(server, "query_run", {"template": "core/models"})).structured_content
    findings = dict(FINDINGS, graph_facts=[{"claim": "Five models.", "steps": []}])
    out = (await call(server, "analyse_bundle", {
        "evidence_ids": [run["evidence_id"]], "question": "q", "findings": findings})).structured_content
    assert out["status"] == "refused" and "cites no step" in out["message"]


async def test_bundle_requires_every_claim_class(server):
    run = (await call(server, "query_run", {"template": "core/models"})).structured_content
    findings = {k: v for k, v in FINDINGS.items() if k != "unknowns"}
    result = await call(server, "analyse_bundle", {
        "evidence_ids": [run["evidence_id"]], "question": "q", "findings": findings})
    assert result.is_error and "unknowns" in result.content[0].text


async def test_bundle_rejects_unknown_evidence(server):
    result = await call(server, "analyse_bundle", {"evidence_ids": ["e-000000000000"],
                                                   "question": "q"})
    assert result.is_error and "Unknown evidence_id" in result.content[0].text
    traversal = await call(server, "analyse_bundle", {"evidence_ids": ["../../etc/passwd"],
                                                      "question": "q"})
    assert traversal.is_error
