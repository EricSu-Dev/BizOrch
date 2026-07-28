import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.knowledge.demo_seed import (
    discover_demo_manifests,
    seed_demo_knowledge,
    seed_demo_knowledge_catalog,
)
from app.knowledge.service import KnowledgeService
from app.persistence.base import Base
from tests.knowledge.fakes import FakeEmbeddings, InMemoryVectorStore


def build_service(tmp_path) -> KnowledgeService:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'demo-knowledge.db'}")
    Base.metadata.create_all(engine)
    sessions: sessionmaker[Session] = sessionmaker(
        bind=engine,
        expire_on_commit=False,
    )
    return KnowledgeService(sessions, FakeEmbeddings(), InMemoryVectorStore())


def test_repository_demo_manifest_is_idempotent_and_searchable(tmp_path) -> None:
    service = build_service(tmp_path)
    repository_root = Path(__file__).resolve().parents[3]
    manifest = (
        repository_root
        / "demo_data"
        / "knowledge"
        / "access_and_security"
        / "manifest.json"
    )

    first = seed_demo_knowledge(service, manifest)
    repeated = seed_demo_knowledge(service, manifest)

    assert len(first) == 3
    assert [item.document_id for item in repeated] == [
        item.document_id for item in first
    ]
    assert all(item.index_status.value == "INDEXED" for item in first)

    result = service.search(
        query="CRM read_only临时权限最长可以申请多少天？",
        knowledge_space="access_and_security",
        actor_id="EMP-1001",
        actor_roles=frozenset({"employee"}),
        top_k=5,
    )

    assert result.citations
    assert any("90天" in citation.excerpt for citation in result.citations)
    assert all(citation.source_uri.startswith("demo-policy://") for citation in result.citations)


def test_demo_knowledge_catalog_is_idempotent_and_role_filtered(tmp_path) -> None:
    service = build_service(tmp_path)
    repository_root = Path(__file__).resolve().parents[3]
    knowledge_root = repository_root / "demo_data" / "knowledge"

    manifests = discover_demo_manifests(knowledge_root)
    assert [path.parent.name for path in manifests] == [
        "access_and_security",
        "employee_services",
        "equipment_maintenance",
        "procurement",
    ]

    first = seed_demo_knowledge_catalog(service, knowledge_root)
    repeated = seed_demo_knowledge_catalog(service, knowledge_root)

    assert len(first) == 15
    assert [item.document_id for item in repeated] == [
        item.document_id for item in first
    ]
    equipment_documents = [
        item for item in first if item.knowledge_space == "equipment_maintenance"
    ]
    assert len(equipment_documents) == 4
    assert all(item.index_status.value == "INDEXED" for item in equipment_documents)
    procurement_documents = [
        item for item in first if item.knowledge_space == "procurement"
    ]
    assert len(procurement_documents) == 4
    assert all(item.index_status.value == "INDEXED" for item in procurement_documents)

    procurement_employee_result = service.search(
        query="办公采购需要填写哪些申请信息？",
        knowledge_space="procurement",
        actor_id="EMP-1001",
        actor_roles=frozenset({"employee"}),
        top_k=10,
    )
    assert any(
        citation.source_uri == "bizorch://policy/procurement/governance/2026.1"
        for citation in procurement_employee_result.citations
    )
    assert all(
        citation.source_uri != "bizorch://policy/procurement/emergency/2026.1"
        for citation in procurement_employee_result.citations
    )

    procurement_approver_result = service.search(
        query="紧急采购是否可以跳过预算检查？",
        knowledge_space="procurement",
        actor_id="EMP-BUDGET-OWNER",
        actor_roles=frozenset({"approver"}),
        top_k=10,
    )
    assert any(
        citation.source_uri == "bizorch://policy/procurement/emergency/2026.1"
        for citation in procurement_approver_result.citations
    )

    employee_result = service.search(
        query="维修完成以后复机验收要检查什么？",
        knowledge_space="equipment_maintenance",
        actor_id="EMP-2001",
        actor_roles=frozenset({"employee"}),
        top_k=10,
    )
    assert employee_result.citations
    assert all(
        citation.source_uri != "demo-maintenance://operations/restart-acceptance"
        for citation in employee_result.citations
    )

    approver_result = service.search(
        query="维修完成以后复机验收要检查什么？",
        knowledge_space="equipment_maintenance",
        actor_id="EMP-MAINT-MANAGER",
        actor_roles=frozenset({"approver"}),
        top_k=10,
    )
    assert any(
        citation.source_uri == "demo-maintenance://operations/restart-acceptance"
        and "防护装置" in citation.excerpt
        for citation in approver_result.citations
    )

    safety_result = service.search(
        query="设备冒烟时是否应该等待AI审批？",
        knowledge_space="equipment_maintenance",
        actor_id="EMP-2001",
        actor_roles=frozenset({"employee"}),
        top_k=5,
    )
    assert any(
        citation.source_uri
        == "demo-maintenance://safety/shutdown-energy-isolation"
        and "现场应急程序" in citation.excerpt
        for citation in safety_result.citations
    )

    onboarding_result = service.search(
        query="入职账号什么时候才可以激活？",
        knowledge_space="employee_services",
        actor_id="EMP-HR-OPERATOR",
        actor_roles=frozenset({"hr"}),
        top_k=5,
    )
    assert any(
        citation.source_uri
        == "demo-employee://hr/onboarding-account-provisioning"
        and "账号不得启用" in citation.excerpt
        for citation in onboarding_result.citations
    )

    offboarding_result = service.search(
        query="离职后账号和权限能否由系统自动恢复？",
        knowledge_space="employee_services",
        actor_id="EMP-HR-OPERATOR",
        actor_roles=frozenset({"hr"}),
        top_k=5,
    )
    assert any(
        citation.source_uri
        == "demo-employee://hr/offboarding-asset-handover"
        and "不得由自动化流程自行恢复" in citation.excerpt
        for citation in offboarding_result.citations
    )


def test_manifest_cannot_read_a_file_outside_its_directory(tmp_path) -> None:
    service = build_service(tmp_path)
    manifest_dir = tmp_path / "manifest"
    manifest_dir.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("outside", encoding="utf-8")
    manifest = manifest_dir / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "manifest_version": "1",
                "documents": [
                    {
                        "path": "../outside.md",
                        "knowledge_space": "access_and_security",
                        "title": "Outside",
                        "source_uri": "demo-policy://outside",
                        "version_label": "1",
                        "source_department": "Security",
                        "trust_level": "HIGH",
                        "effective_from": "2026-01-01T00:00:00Z",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="escapes"):
        seed_demo_knowledge(service, manifest)
