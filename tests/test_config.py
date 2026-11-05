from __future__ import annotations

import pytest

from linked_archi_mcp.config import Config, ConfigError, Target, is_bundled_profile


def test_target_is_one_of_data_or_endpoint():
    with pytest.raises(ConfigError):
        Target(("a.trig",), "https://x/sparql")


def test_missing_target_is_explained():
    with pytest.raises(ConfigError, match="No dataset"):
        Config().target_for(None, None)


def test_locked_server_refuses_client_targets_and_paths():
    config = Config(target=Target(("/srv/g.trig",)), allow_client_paths=False)
    assert config.target_for(None, None).data == ("/srv/g.trig",)
    with pytest.raises(ConfigError, match="does not accept"):
        config.target_for(["/etc/passwd"], None)
    with pytest.raises(ConfigError, match="does not accept"):
        config.target_for(None, "https://internal.example/sparql")
    with pytest.raises(ConfigError, match="is a path"):
        config.profile_for("/tmp/evil.yaml")
    assert config.profile_for("examples/curated-store") == "examples/curated-store"
    with pytest.raises(ConfigError):
        config.paths_for("shapes", ["/etc/shadow"], ())


@pytest.mark.parametrize("name,ok", [
    ("linked-archi-default", True),
    ("examples/cloudplatform", True),
    ("../x", False),
    ("profiles/acme.yaml", False),
    ("acme.yml", False),
    ("/abs/acme", False),
])
def test_bundled_profile_names(name, ok):
    assert is_bundled_profile(name) is ok
