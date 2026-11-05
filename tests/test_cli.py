from __future__ import annotations

from linked_archi_mcp.cli import build_config, main, parser


def test_client_paths_default_by_transport():
    assert build_config(parser().parse_args(["--data", "g.trig"])).allow_client_paths
    assert not build_config(parser().parse_args(["http", "--data", "g.trig"])).allow_client_paths
    assert build_config(parser().parse_args(
        ["http", "--allow-client-paths", "--data", "g.trig"])).allow_client_paths


def test_env_data_is_the_default(monkeypatch):
    monkeypatch.setenv("LINKED_ARCHI_DATA", "/a.trig")
    assert build_config(parser().parse_args([])).target.data == ("/a.trig",)
    assert build_config(parser().parse_args(["--data", "/b.trig"])).target.data == ("/b.trig",)


def test_http_refuses_public_bind_without_token(monkeypatch, capsys):
    monkeypatch.delenv("LINKED_ARCHI_MCP_TOKEN", raising=False)
    assert main(["http", "--host", "0.0.0.0", "--data", "g.trig"]) == 2
    assert "LINKED_ARCHI_MCP_TOKEN" in capsys.readouterr().err


def test_both_targets_is_a_usage_error(capsys):
    assert main(["--data", "g.trig", "--endpoint", "https://x/sparql"]) == 2
