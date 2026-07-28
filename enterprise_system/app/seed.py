"""Small deterministic dataset for local development and demonstrations."""

from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from enterprise_system.app.contracts import (
    AssetTaskStatus,
    AssetTaskType,
    CorporateAccountStatus,
    EmployeeLifecycleRequestStatus,
    EmployeeLifecycleRequestType,
    EmploymentStatus,
    EquipmentCriticality,
    EquipmentStatus,
    ProcurementRequestStatus,
)
from enterprise_system.app.models import (
    AccessPackageRecord,
    ApplicationRecord,
    AssetTaskRecord,
    CorporateAccountRecord,
    CostCenterRecord,
    EmployeeRecord,
    EmployeeLifecycleRequestRecord,
    EquipmentRecord,
    JobProfileRecord,
    MaintenanceHistoryRecord,
    OrganizationUnitRecord,
    ProcurementPolicyRecord,
    ProcurementRequestItemRecord,
    ProcurementRequestRecord,
    WorkLocationRecord,
)


def seed_demo_data(session: Session) -> None:
    """Insert stable demo identities and applications when absent."""
    organization_units = (
        OrganizationUnitRecord(
            department_code="SALES-EAST",
            display_name="华东销售部",
            manager_id="EMP-MANAGER",
            active=True,
            version=1,
        ),
        OrganizationUnitRecord(
            department_code="PLANT-WORKSHOP-1",
            display_name="第一生产车间",
            manager_id="EMP-MAINT-MANAGER",
            active=True,
            version=1,
        ),
        OrganizationUnitRecord(
            department_code="PLANT-MAINTENANCE",
            display_name="设备维护部",
            manager_id="EMP-MAINT-MANAGER",
            active=True,
            version=1,
        ),
        OrganizationUnitRecord(
            department_code="PRODUCTION-MANAGEMENT",
            display_name="生产管理部",
            manager_id="EMP-MANAGER",
            active=True,
            version=1,
        ),
        OrganizationUnitRecord(
            department_code="EQUIPMENT-ENGINEERING",
            display_name="设备工程部",
            manager_id="EMP-MAINT-MANAGER",
            active=True,
            version=1,
        ),
        OrganizationUnitRecord(
            department_code="FORMER",
            display_name="历史人员归档",
            manager_id="EMP-MANAGER",
            active=False,
            version=1,
        ),
        OrganizationUnitRecord(
            department_code="FINANCE-CONTROL",
            display_name="财务预算管理部",
            manager_id="EMP-BUDGET-OWNER",
            active=True,
            version=1,
        ),
        OrganizationUnitRecord(
            department_code="PROCUREMENT-OPERATIONS",
            display_name="采购运营部",
            manager_id="EMP-PROCUREMENT-OWNER",
            active=True,
            version=1,
        ),
    )
    for unit in organization_units:
        if (
            session.get(OrganizationUnitRecord, unit.department_code)
            is None
        ):
            session.add(unit)
    session.flush()

    work_locations = (
        WorkLocationRecord(
            location_code="SHANGHAI-HQ",
            display_name="上海总部",
            active=True,
            version=1,
        ),
        WorkLocationRecord(
            location_code="PLANT-EAST",
            display_name="华东生产基地",
            active=True,
            version=1,
        ),
        WorkLocationRecord(
            location_code="ARCHIVED",
            display_name="历史归档地点",
            active=False,
            version=1,
        ),
    )
    for location in work_locations:
        if session.get(WorkLocationRecord, location.location_code) is None:
            session.add(location)
    session.flush()

    access_packages = (
        AccessPackageRecord(
            package_code="SALES-STANDARD",
            display_name="销售岗位标准权限",
            role_bindings=[
                {"application_code": "CRM", "role_code": "standard"},
                {"application_code": "ERP", "role_code": "read_only"},
            ],
            version=1,
            active=True,
        ),
        AccessPackageRecord(
            package_code="PLANT-OPERATOR",
            display_name="生产操作岗位标准权限",
            role_bindings=[
                {"application_code": "ERP", "role_code": "read_only"}
            ],
            version=1,
            active=True,
        ),
        AccessPackageRecord(
            package_code="MAINTENANCE-ENGINEER",
            display_name="设备维护岗位标准权限",
            role_bindings=[
                {"application_code": "ERP", "role_code": "standard"}
            ],
            version=1,
            active=True,
        ),
        AccessPackageRecord(
            package_code="PRODUCTION-PLANNER",
            display_name="生产计划岗位标准权限",
            role_bindings=[
                {"application_code": "ERP", "role_code": "standard"}
            ],
            version=1,
            active=True,
        ),
        AccessPackageRecord(
            package_code="NO-ACCESS",
            display_name="无基线权限",
            role_bindings=[],
            version=1,
            active=False,
        ),
        AccessPackageRecord(
            package_code="FINANCE-STANDARD",
            display_name="财务预算岗位标准权限",
            role_bindings=[
                {"application_code": "ERP", "role_code": "standard"}
            ],
            version=1,
            active=True,
        ),
        AccessPackageRecord(
            package_code="PROCUREMENT-STANDARD",
            display_name="采购运营岗位标准权限",
            role_bindings=[
                {"application_code": "ERP", "role_code": "standard"}
            ],
            version=1,
            active=True,
        ),
    )
    for package in access_packages:
        if session.get(AccessPackageRecord, package.package_code) is None:
            session.add(package)

    job_profiles = (
        JobProfileRecord(
            job_code="SALES-MANAGER",
            display_name="销售经理",
            department_code="SALES-EAST",
            baseline_access_package_code="SALES-STANDARD",
            asset_profile_code="OFFICE-MANAGER",
            active=True,
            version=1,
        ),
        JobProfileRecord(
            job_code="SALES-SPECIALIST",
            display_name="销售专员",
            department_code="SALES-EAST",
            baseline_access_package_code="SALES-STANDARD",
            asset_profile_code="OFFICE-LAPTOP",
            active=True,
            version=1,
        ),
        JobProfileRecord(
            job_code="MAINTENANCE-MANAGER",
            display_name="设备维护经理",
            department_code="PLANT-MAINTENANCE",
            baseline_access_package_code="MAINTENANCE-ENGINEER",
            asset_profile_code="INDUSTRIAL-LAPTOP",
            active=True,
            version=1,
        ),
        JobProfileRecord(
            job_code="PLANT-OPERATOR",
            display_name="生产操作员",
            department_code="PLANT-WORKSHOP-1",
            baseline_access_package_code="PLANT-OPERATOR",
            asset_profile_code="SHOP-FLOOR-TERMINAL",
            active=True,
            version=1,
        ),
        JobProfileRecord(
            job_code="PRODUCTION-PLANNER",
            display_name="生产计划专员",
            department_code="PRODUCTION-MANAGEMENT",
            baseline_access_package_code="PRODUCTION-PLANNER",
            asset_profile_code="OFFICE-LAPTOP",
            active=True,
            version=1,
        ),
        JobProfileRecord(
            job_code="EQUIPMENT-MAINTENANCE-ENGINEER",
            display_name="设备维护工程师",
            department_code="EQUIPMENT-ENGINEERING",
            baseline_access_package_code="MAINTENANCE-ENGINEER",
            asset_profile_code="INDUSTRIAL-LAPTOP",
            active=True,
            version=1,
        ),
        JobProfileRecord(
            job_code="FORMER-EMPLOYEE",
            display_name="历史员工",
            department_code="FORMER",
            baseline_access_package_code="NO-ACCESS",
            asset_profile_code="NONE",
            active=False,
            version=1,
        ),
        JobProfileRecord(
            job_code="BUDGET-CONTROLLER",
            display_name="预算负责人",
            department_code="FINANCE-CONTROL",
            baseline_access_package_code="FINANCE-STANDARD",
            asset_profile_code="OFFICE-LAPTOP",
            active=True,
            version=1,
        ),
        JobProfileRecord(
            job_code="PROCUREMENT-CONTROLLER",
            display_name="采购负责人",
            department_code="PROCUREMENT-OPERATIONS",
            baseline_access_package_code="PROCUREMENT-STANDARD",
            asset_profile_code="OFFICE-LAPTOP",
            active=True,
            version=1,
        ),
    )
    for profile in job_profiles:
        if session.get(JobProfileRecord, profile.job_code) is None:
            session.add(profile)
    session.flush()

    employees = (
        EmployeeRecord(
            employee_id="EMP-MANAGER",
            display_name="Chen Manager",
            department_code="SALES-EAST",
            manager_id=None,
            active=True,
            employment_status=EmploymentStatus.ACTIVE.value,
            job_code="SALES-MANAGER",
            work_location_code="SHANGHAI-HQ",
            version=1,
        ),
        EmployeeRecord(
            employee_id="EMP-1001",
            display_name="Lin Employee",
            department_code="SALES-EAST",
            manager_id="EMP-MANAGER",
            active=True,
            employment_status=EmploymentStatus.ACTIVE.value,
            job_code="SALES-SPECIALIST",
            work_location_code="SHANGHAI-HQ",
            version=1,
        ),
        EmployeeRecord(
            employee_id="EMP-INACTIVE",
            display_name="Former Employee",
            department_code="FORMER",
            manager_id=None,
            active=False,
            employment_status=EmploymentStatus.INACTIVE.value,
            job_code="FORMER-EMPLOYEE",
            work_location_code="ARCHIVED",
            version=1,
        ),
        EmployeeRecord(
            employee_id="EMP-MAINT-MANAGER",
            display_name="Zhou Maintenance Manager",
            department_code="PLANT-MAINTENANCE",
            manager_id=None,
            active=True,
            employment_status=EmploymentStatus.ACTIVE.value,
            job_code="MAINTENANCE-MANAGER",
            work_location_code="PLANT-EAST",
            version=1,
        ),
        EmployeeRecord(
            employee_id="EMP-2001",
            display_name="Wang Plant Operator",
            department_code="PLANT-WORKSHOP-1",
            manager_id="EMP-MAINT-MANAGER",
            active=True,
            employment_status=EmploymentStatus.ACTIVE.value,
            job_code="PLANT-OPERATOR",
            work_location_code="PLANT-EAST",
            version=1,
        ),
        EmployeeRecord(
            employee_id="EMP-BUDGET-OWNER",
            display_name="Yu Budget Owner",
            department_code="FINANCE-CONTROL",
            manager_id=None,
            active=True,
            employment_status=EmploymentStatus.ACTIVE.value,
            job_code="BUDGET-CONTROLLER",
            work_location_code="SHANGHAI-HQ",
            version=1,
        ),
        EmployeeRecord(
            employee_id="EMP-PROCUREMENT-OWNER",
            display_name="Qian Procurement Owner",
            department_code="PROCUREMENT-OPERATIONS",
            manager_id=None,
            active=True,
            employment_status=EmploymentStatus.ACTIVE.value,
            job_code="PROCUREMENT-CONTROLLER",
            work_location_code="SHANGHAI-HQ",
            version=1,
        ),
    )
    for employee in employees:
        if session.get(EmployeeRecord, employee.employee_id) is None:
            session.add(employee)
    session.flush()

    accounts = (
        ("30000000-0000-4000-8000-000000000001", "EMP-MANAGER", "manager", "ACTIVE"),
        ("30000000-0000-4000-8000-000000000002", "EMP-1001", "employee", "ACTIVE"),
        ("30000000-0000-4000-8000-000000000003", "EMP-INACTIVE", "former", "DISABLED"),
        (
            "30000000-0000-4000-8000-000000000004",
            "EMP-MAINT-MANAGER",
            "maint.manager",
            "ACTIVE",
        ),
        ("30000000-0000-4000-8000-000000000005", "EMP-2001", "plant.operator", "ACTIVE"),
        (
            "30000000-0000-4000-8000-000000000006",
            "EMP-BUDGET-OWNER",
            "budget.owner",
            "ACTIVE",
        ),
        (
            "30000000-0000-4000-8000-000000000007",
            "EMP-PROCUREMENT-OWNER",
            "procurement.owner",
            "ACTIVE",
        ),
    )
    for account_id, employee_id, username, account_status in accounts:
        if session.get(CorporateAccountRecord, account_id) is None:
            session.add(
                CorporateAccountRecord(
                    account_id=account_id,
                    employee_id=employee_id,
                    username=username,
                    status=CorporateAccountStatus(account_status).value,
                    version=1,
                )
            )

    if (
        session.get(
            AssetTaskRecord,
            "40000000-0000-4000-8000-000000000001",
        )
        is None
    ):
        session.add(
            AssetTaskRecord(
                task_id="40000000-0000-4000-8000-000000000001",
                employee_id="EMP-2001",
                task_type=AssetTaskType.PROVISION.value,
                asset_profile_code="SHOP-FLOOR-TERMINAL",
                status=AssetTaskStatus.COMPLETED.value,
                idempotency_key="seed-asset-emp-2001",
            )
        )
    if (
        session.get(
            EmployeeLifecycleRequestRecord,
            "50000000-0000-4000-8000-000000000001",
        )
        is None
    ):
        session.add(
            EmployeeLifecycleRequestRecord(
                request_id="50000000-0000-4000-8000-000000000001",
                request_type=EmployeeLifecycleRequestType.TRANSFER.value,
                subject_employee_id="EMP-1001",
                initiator_id="EMP-HR-OPERATOR",
                status=EmployeeLifecycleRequestStatus.COMPLETED.value,
                effective_date=date(2026, 6, 1),
                safe_summary="历史调岗演示记录",
                idempotency_key="seed-lifecycle-emp-1001",
            )
        )

    cost_centers = (
        CostCenterRecord(
            cost_center_code="CC-SALES-EAST-001",
            display_name="华东销售办公费用中心",
            department_code="SALES-EAST",
            budget_owner_id="EMP-BUDGET-OWNER",
            currency="CNY",
            budget_total=Decimal("100000.00"),
            spent_amount=Decimal("25000.00"),
            reserved_amount=Decimal("5000.00"),
            active=True,
            version=1,
        ),
        CostCenterRecord(
            cost_center_code="CC-PLANT-001",
            display_name="第一生产基地费用中心",
            department_code="PLANT-WORKSHOP-1",
            budget_owner_id="EMP-BUDGET-OWNER",
            currency="CNY",
            budget_total=Decimal("30000.00"),
            spent_amount=Decimal("26000.00"),
            reserved_amount=Decimal("1000.00"),
            active=True,
            version=2,
        ),
        CostCenterRecord(
            cost_center_code="CC-FORMER-001",
            display_name="历史归档费用中心",
            department_code="FORMER",
            budget_owner_id="EMP-BUDGET-OWNER",
            currency="CNY",
            budget_total=Decimal("1.00"),
            spent_amount=Decimal("0.00"),
            reserved_amount=Decimal("0.00"),
            active=False,
            version=1,
        ),
    )
    for cost_center in cost_centers:
        if session.get(CostCenterRecord, cost_center.cost_center_code) is None:
            session.add(cost_center)

    if session.get(ProcurementPolicyRecord, "OFFICE-PROCUREMENT-2026") is None:
        session.add(
            ProcurementPolicyRecord(
                policy_code="OFFICE-PROCUREMENT-2026",
                version=1,
                currency="CNY",
                level_one_limit=Decimal("5000.00"),
                level_two_limit=Decimal("50000.00"),
                procurement_approver_id="EMP-PROCUREMENT-OWNER",
                allowed_item_categories=[
                    "OFFICE_SUPPLIES",
                    "OFFICE_EQUIPMENT",
                    "OFFICE_FURNITURE",
                ],
                active=True,
                effective_from=date(2026, 1, 1),
            )
        )
    session.flush()

    procurement_request_id = "60000000-0000-4000-8000-000000000001"
    if session.get(ProcurementRequestRecord, procurement_request_id) is None:
        session.add(
            ProcurementRequestRecord(
                request_id=procurement_request_id,
                external_workflow_run_id="61000000-0000-4000-8000-000000000001",
                requester_id="EMP-1001",
                cost_center_code="CC-SALES-EAST-001",
                estimated_total_amount=Decimal("2600.00"),
                currency="CNY",
                desired_date=date(2026, 8, 5),
                delivery_location_code="SHANGHAI-HQ",
                business_reason_summary="销售项目现场资料整理需要补充标准办公显示设备。",
                status=ProcurementRequestStatus.PENDING_APPROVAL.value,
                policy_code="OFFICE-PROCUREMENT-2026",
                policy_version=1,
                idempotency_key="seed-procurement-emp-1001",
            )
        )
        session.flush()
        session.add_all(
            (
                ProcurementRequestItemRecord(
                    request_id=procurement_request_id,
                    line_no=1,
                    item_name="27 英寸办公显示器",
                    item_category="OFFICE_EQUIPMENT",
                    quantity=1,
                    specification_note="标准办公显示设备",
                ),
                ProcurementRequestItemRecord(
                    request_id=procurement_request_id,
                    line_no=2,
                    item_name="无线键鼠套装",
                    item_category="OFFICE_EQUIPMENT",
                    quantity=1,
                    specification_note=None,
                ),
            )
        )
    session.flush()

    applications = (
        ApplicationRecord(
            application_code="CRM",
            display_name="Customer Relationship Management",
            active=True,
            allowed_role_codes=["read_only", "standard", "admin"],
        ),
        ApplicationRecord(
            application_code="ERP",
            display_name="Enterprise Resource Planning",
            active=True,
            allowed_role_codes=["read_only", "standard"],
        ),
    )
    for application in applications:
        if session.get(ApplicationRecord, application.application_code) is None:
            session.add(application)

    equipment = (
        EquipmentRecord(
            equipment_id="10000000-0000-4000-8000-000000000001",
            equipment_code="PRESS-001",
            name="一号冲压机",
            site_code="PLANT-EAST",
            workshop_code="WORKSHOP-1",
            production_line="STAMPING-LINE-1",
            criticality=EquipmentCriticality.HIGH.value,
            status=EquipmentStatus.RUNNING.value,
            responsible_manager_id="EMP-MAINT-MANAGER",
            version=1,
        ),
        EquipmentRecord(
            equipment_id="10000000-0000-4000-8000-000000000002",
            equipment_code="PRESS-002",
            name="二号冲压机",
            site_code="PLANT-EAST",
            workshop_code="WORKSHOP-1",
            production_line="STAMPING-LINE-1",
            criticality=EquipmentCriticality.MEDIUM.value,
            status=EquipmentStatus.DEGRADED.value,
            responsible_manager_id="EMP-MAINT-MANAGER",
            version=3,
        ),
        EquipmentRecord(
            equipment_id="10000000-0000-4000-8000-000000000003",
            equipment_code="PUMP-001",
            name="冷却循环泵",
            site_code="PLANT-EAST",
            workshop_code="UTILITY-1",
            production_line="COOLING-SYSTEM",
            criticality=EquipmentCriticality.HIGH.value,
            status=EquipmentStatus.MAINTENANCE_PENDING.value,
            responsible_manager_id="EMP-MAINT-MANAGER",
            version=2,
        ),
    )
    for record in equipment:
        if session.get(EquipmentRecord, record.equipment_id) is None:
            session.add(record)
    session.flush()

    history = (
        MaintenanceHistoryRecord(
            record_id="20000000-0000-4000-8000-000000000001",
            equipment_id="10000000-0000-4000-8000-000000000001",
            fault_summary="滑块运行时出现间歇性异响",
            resolution_summary="紧固传动机构连接件并完成空载试运行",
            completed_at=datetime(2026, 5, 12, 8, 30, tzinfo=UTC),
        ),
        MaintenanceHistoryRecord(
            record_id="20000000-0000-4000-8000-000000000002",
            equipment_id="10000000-0000-4000-8000-000000000001",
            fault_summary="主轴振动值超过日常点检基线",
            resolution_summary="更换磨损轴承并重新校准主轴",
            completed_at=datetime(2026, 6, 20, 10, 15, tzinfo=UTC),
        ),
        MaintenanceHistoryRecord(
            record_id="20000000-0000-4000-8000-000000000003",
            equipment_id="10000000-0000-4000-8000-000000000002",
            fault_summary="润滑压力短时偏低",
            resolution_summary="清理过滤器并补充规定型号润滑油",
            completed_at=datetime(2026, 6, 2, 9, 0, tzinfo=UTC),
        ),
    )
    for record in history:
        if session.get(MaintenanceHistoryRecord, record.record_id) is None:
            session.add(record)
    session.flush()
