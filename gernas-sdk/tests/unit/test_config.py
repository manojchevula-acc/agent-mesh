"""Configuration: mandatory identity is validated, never invented; values come from GERNAS_* only."""

from __future__ import annotations

import json

import pytest

from gernas.attributes import Ctx
from gernas.config import GernasConfig, GernasConfigError

from conftest import IDENTITY, run_python


def _env(**overrides):
    env = {**IDENTITY, "GERNAS_EXPORTER": "off", **overrides}
    return {k: v for k, v in env.items() if v is not None}


def test_from_env_reads_identity_and_defaults():
    cfg = GernasConfig.from_env(environ=_env(GERNAS_SERVICE_VERSION="1.2.3"))
    assert (cfg.service_name, cfg.use_case_id, cfg.application_id, cfg.business_domain, cfg.environment) == (
        "svc",
        "UC-1",
        "app-1",
        "retail_banking",
        "dev",
    )
    assert cfg.agent_version == "1.2.3"  # defaults to the service version
    assert cfg.envelope == {
        Ctx.BUSINESS_DOMAIN: "retail_banking",
        Ctx.USE_CASE_ID: "UC-1",
        Ctx.APPLICATION_ID: "app-1",
        Ctx.ENVIRONMENT: "dev",
    }


@pytest.mark.parametrize("missing", list(IDENTITY))
def test_each_mandatory_identity_variable_is_enforced(missing):
    with pytest.raises(GernasConfigError, match=missing):
        GernasConfig.from_env(environ=_env(**{missing: None}))


def test_all_missing_variables_are_named_at_once():
    with pytest.raises(GernasConfigError) as err:
        GernasConfig.from_env(environ={"GERNAS_EXPORTER": "off"})
    for name in IDENTITY:
        assert name in str(err.value)


def test_service_name_argument_overrides_env():
    assert GernasConfig.from_env("other", environ=_env()).service_name == "other"


def test_otlp_requires_an_endpoint():
    with pytest.raises(GernasConfigError, match="GERNAS_OTLP_ENDPOINT"):
        GernasConfig.from_env(environ=_env(GERNAS_EXPORTER="otlp"))
    cfg = GernasConfig.from_env(environ=_env(GERNAS_EXPORTER=None, GERNAS_OTLP_ENDPOINT="http://127.0.0.1:4317"))
    assert cfg.exporter == "otlp"


@pytest.mark.parametrize(
    "var,value",
    [
        ("GERNAS_EXPORTER", "file"),
        ("GERNAS_OTLP_TRANSPORT", "udp"),
        ("GERNAS_SAMPLING_RATE", "2"),
        ("GERNAS_MAX_STEPS", "lots"),
    ],
)
def test_invalid_values_are_rejected(var, value):
    with pytest.raises(GernasConfigError, match=var):
        GernasConfig.from_env(environ=_env(**{var: value}))


def test_pod_is_derived_only_inside_kubernetes():
    assert GernasConfig.from_env(environ=_env(HOSTNAME="laptop")).pod == ""
    cfg = GernasConfig.from_env(environ=_env(HOSTNAME="agent-7f9c", KUBERNETES_SERVICE_HOST="10.0.0.1"))
    assert cfg.pod == "agent-7f9c"
    assert dict(cfg.resource.attributes)[Ctx.POD] == "agent-7f9c"


def test_resource_carries_identity_and_sdk_version():
    res = dict(
        GernasConfig.from_env(environ=_env(GERNAS_CLUSTER="aks-1", GERNAS_REGION="uaenorth")).resource.attributes
    )
    assert res["service.name"] == "svc" and res[Ctx.SDK_VERSION]
    assert res[Ctx.CLUSTER] == "aks-1" and res[Ctx.REGION] == "uaenorth" and Ctx.NAMESPACE not in res


def test_init_fails_loudly_and_decorators_invent_nothing_without_config():
    code = r"""
import json, gernas
from gernas.tracer import get_config
out = {"config": get_config() is None}
try:
    gernas.init_gernas()
except gernas.GernasConfigError as exc:
    out["error"] = str(exc)

@gernas.trace_agent("a", agent_type="worker")
def run():
    return 1
out["result"] = run()
print(json.dumps(out))
"""
    result = run_python(code)
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout.strip().splitlines()[-1])
    assert out["config"] is True and "GERNAS_SERVICE_NAME" in out["error"] and out["result"] == 1


def test_init_is_idempotent(sdk):
    import gernas

    assert gernas.init_gernas() is sdk
