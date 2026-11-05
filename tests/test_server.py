"""End to end: the MCP client drives the server in-process against the skills' fixtures."""

from __future__ import annotations

from typing import Any

import pytest
from mcp import Client

from linked_archi_mcp.config import Config, Target
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
def server(config, skills):
    return build_server(config, skills)


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
