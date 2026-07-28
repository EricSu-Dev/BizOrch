from app.integrations.mcp_client import ReadOnlyEnterpriseOpsMcpClient
from app.scenarios.access_management.composition import build_mcp_enterprise_clients


def test_composition_separates_read_only_agent_capability_from_full_client() -> None:
    full_client, read_only = build_mcp_enterprise_clients(
        "http://enterprise-ops-mcp:8200/mcp"
    )

    assert isinstance(read_only, ReadOnlyEnterpriseOpsMcpClient)
    assert hasattr(full_client, "grant_application_access")
    assert not hasattr(read_only, "grant_application_access")
    assert not hasattr(read_only, "create_access_request")

