"""Static production Compose boundaries that do not require a Docker daemon."""

from pathlib import Path

import yaml


COMPOSE_PATH = Path(__file__).resolve().parents[3] / "deploy" / "compose.yaml"


def _compose() -> dict[str, object]:
    return yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))


def test_production_compose_uses_prebuilt_images_and_one_public_api_port() -> None:
    payload = _compose()
    services = payload["services"]

    assert all("build" not in service for service in services.values())
    assert all("env_file" not in service for service in services.values())
    assert services["bizorch-api"]["ports"] == [
        "127.0.0.1:${BIZORCH_API_HOST_PORT:-18000}:8000"
    ]
    assert all(
        "ports" not in service
        for name, service in services.items()
        if name != "bizorch-api"
    )


def test_action_and_enterprise_networks_are_internal_and_worker_isolated() -> None:
    payload = _compose()
    services = payload["services"]
    networks = payload["networks"]

    assert networks["action-backplane"]["internal"] is True
    assert networks["enterprise-backplane"]["internal"] is True
    assert set(services["bizorch-api"]["networks"]) == {
        "api-egress",
        "action-backplane",
    }
    assert set(services["enterprise-ops-mcp"]["networks"]) == {
        "action-backplane",
        "enterprise-backplane",
    }
    assert set(services["enterprise-system"]["networks"]) == {
        "enterprise-data-egress",
        "enterprise-backplane",
    }
    assert services["knowledge-index-worker"]["networks"] == ["api-egress"]


def test_each_process_receives_only_its_required_secret_classes() -> None:
    services = _compose()["services"]
    api_environment = services["bizorch-api"]["environment"]
    mcp_environment = services["enterprise-ops-mcp"]["environment"]
    enterprise_environment = services["enterprise-system"]["environment"]
    contract_environment = services["evaluation-contract"]["environment"]
    knowledge_environment = services["knowledge-index-worker"]["environment"]

    assert "ENTERPRISE_INTERNAL_TOKEN" not in api_environment
    assert set(mcp_environment) == {
        "TZ",
        "ENTERPRISE_OPS_BASE_URL",
        "ENTERPRISE_INTERNAL_TOKEN",
    }
    assert set(enterprise_environment) == {
        "TZ",
        "ENTERPRISE_DATABASE_URL",
        "ENTERPRISE_INTERNAL_TOKEN",
    }
    assert "DEEPSEEK_API_KEY" not in contract_environment
    assert "DASHSCOPE_API_KEY" not in contract_environment
    assert "DEEPSEEK_API_KEY" not in knowledge_environment
    assert "BIZORCH_OSS_ACCESS_KEY_SECRET" not in knowledge_environment


def test_read_only_live_evaluation_mounts_chroma_but_not_action_network() -> None:
    services = _compose()["services"]
    api = services["bizorch-api"]
    knowledge = services["knowledge-index-worker"]
    live = services["evaluation-live"]

    assert live["networks"] == ["api-egress"]
    assert {volume["target"] for volume in api["volumes"]} == {
        "/app/data/chroma",
        "/app/data/checkpoints",
        "/app/data/uploads",
        "/app/logs",
    }
    assert knowledge["volumes"] == live["volumes"]
    assert live["volumes"] == [
        {
            "type": "bind",
            "source": (
                "${BIZORCH_DATA_DIR:?set BIZORCH_DATA_DIR in the Compose env file}"
                "/chroma"
            ),
            "target": "/app/data/chroma",
        }
    ]


def test_worker_commands_use_image_root_entrypoints() -> None:
    services = _compose()["services"]

    assert services["knowledge-index-worker"]["command"] == [
        "python",
        "run_knowledge_index_worker.py",
    ]
    assert services["evaluation-contract"]["command"] == [
        "python",
        "run_evaluation_worker.py",
    ]
    assert services["evaluation-live"]["command"] == [
        "python",
        "run_evaluation_worker.py",
        "--live",
    ]
    assert services["knowledge-index-worker"]["healthcheck"] == {"disable": True}
    assert services["evaluation-contract"]["healthcheck"] == {"disable": True}
    assert services["evaluation-live"]["healthcheck"] == {"disable": True}
