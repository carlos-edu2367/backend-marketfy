import ast
from collections import Counter
import importlib
import importlib.util
from pathlib import Path
import sys

from alembic.config import Config
from alembic.script import ScriptDirectory
import pytest
import sqlalchemy as sa


MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "20260714_0002_product_tax_rule_assignment_history.py"
)
VERSIONS_PATH = MIGRATION_PATH.parent
V2_MIGRATION_PATH = VERSIONS_PATH / "20260715_0008_product_tax_rules_v2.py"
V3_MIGRATION_PATH = VERSIONS_PATH / "20260715_0009_sale_fiscal_rule_pendencies.py"


def migration_revision_ids() -> list[str]:
    revisions = []
    for path in VERSIONS_PATH.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if not any(isinstance(target, ast.Name) and target.id == "revision" for target in targets):
                continue
            value = node.value
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                revisions.append(value.value)
    return revisions


def load_migration_module():
    spec = importlib.util.spec_from_file_location("tax_rule_history_migration", MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def load_v2_migration_module():
    spec = importlib.util.spec_from_file_location(
        "product_tax_rules_v2_migration", V2_MIGRATION_PATH
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def load_v3_migration_module():
    spec = importlib.util.spec_from_file_location(
        "sale_fiscal_rule_pendencies_migration", V3_MIGRATION_PATH
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class OperationRecorder:
    def __init__(self):
        self.executed_sql = []
        self.events = []

    def create_table(self, *args, **kwargs):
        return None

    def create_index(self, *args, **kwargs):
        self.events.append(("create_index", args, kwargs))
        return None

    def drop_index(self, *args, **kwargs):
        self.events.append(("drop_index", args, kwargs))

    def add_column(self, table_name, column):
        self.events.append(("add_column", (table_name, column), {}))

    def drop_column(self, *args, **kwargs):
        self.events.append(("drop_column", args, kwargs))

    def alter_column(self, *args, **kwargs):
        self.events.append(("alter_column", args, kwargs))

    def create_unique_constraint(self, *args, **kwargs):
        self.events.append(("create_unique_constraint", args, kwargs))

    def drop_constraint(self, *args, **kwargs):
        self.events.append(("drop_constraint", args, kwargs))

    def create_foreign_key(self, *args, **kwargs):
        self.events.append(("create_foreign_key", args, kwargs))

    def execute(self, sql):
        self.executed_sql.append(str(sql))


def test_upgrade_backfills_only_explicit_current_product_rule_links(monkeypatch) -> None:
    migration = load_migration_module()
    recorder = OperationRecorder()
    monkeypatch.setattr(migration, "op", recorder)

    migration.upgrade()

    backfills = [sql for sql in recorder.executed_sql if "product_tax_rule_assignments" in sql]
    assert len(backfills) == 1
    backfill = backfills[0]
    assert "FROM products p" in backfill
    assert "p.tax_rule_id IS NOT NULL" in backfill
    assert "product_tax_profiles" not in backfill
    assert "CURRENT_DATE" in backfill
    assert "GREATEST" not in backfill
    assert "r.effective_from" not in backfill


def test_alembic_revision_ids_are_unique() -> None:
    revisions = migration_revision_ids()
    duplicates = sorted(
        revision for revision, count in Counter(revisions).items() if count > 1
    )

    assert duplicates == []


def test_alembic_graph_has_single_head() -> None:
    config = Config(str(VERSIONS_PATH.parents[1] / "alembic.ini"))
    script = ScriptDirectory.from_config(config)

    assert script.get_heads() == ["20260720_0010"]


def test_tax_rule_v2_migration_follows_repaired_marketfy_head() -> None:
    migration = load_v2_migration_module()

    assert migration.revision == "20260715_0008"
    assert migration.down_revision == "20260715_0007"


def test_sale_fiscal_pendencies_migration_is_nullable_json_successor(
    monkeypatch,
) -> None:
    migration = load_v3_migration_module()
    recorder = OperationRecorder()
    monkeypatch.setattr(migration, "op", recorder)

    migration.upgrade()

    assert migration.revision == "20260715_0009"
    assert migration.down_revision == "20260715_0008"
    event, args, _kwargs = recorder.events[0]
    assert event == "add_column"
    assert args[0] == "sales"
    assert args[1].name == "fiscal_rule_pendencies_json"
    assert isinstance(args[1].type, sa.JSON)
    assert args[1].nullable is True
    assert recorder.executed_sql == []

    recorder.events.clear()
    migration.downgrade()
    assert recorder.events == [
        ("drop_column", ("sales", "fiscal_rule_pendencies_json"), {})
    ]


def test_tax_rule_v2_upgrade_adds_only_missing_nullable_evidence(monkeypatch) -> None:
    migration = load_v2_migration_module()
    recorder = OperationRecorder()
    monkeypatch.setattr(migration, "op", recorder)

    migration.upgrade()

    added_columns = {
        (args[0], args[1].name): args[1]
        for event, args, _kwargs in recorder.events
        if event == "add_column"
    }
    assert set(added_columns) == {
        *(('product_tax_rules', name) for name in (
            "issuer_regime",
            "destination_uf",
            "document_model",
            "cbenef",
            "tax_parameters_json",
            "approval_json",
            "published_at",
            "retired_at",
        )),
        ("sale_items", "tax_rule_id_snapshot"),
        ("fiscal_documents", "request_contract_version"),
        ("fiscal_documents", "request_payload_json"),
        ("fiscal_documents", "request_payload_sha256"),
    }
    assert all(column.nullable for column in added_columns.values())
    assert isinstance(
        added_columns[("product_tax_rules", "tax_parameters_json")].type, sa.JSON
    )
    assert isinstance(
        added_columns[("product_tax_rules", "approval_json")].type, sa.JSON
    )
    assert isinstance(
        added_columns[("fiscal_documents", "request_payload_json")].type, sa.JSON
    )


def test_tax_rule_v2_upgrade_converts_snapshot_json_and_restricts_rule_links(
    monkeypatch,
) -> None:
    migration = load_v2_migration_module()
    recorder = OperationRecorder()
    monkeypatch.setattr(migration, "op", recorder)

    migration.upgrade()

    snapshot_alter = next(
        (args, kwargs)
        for event, args, kwargs in recorder.events
        if event == "alter_column"
        and args[:2] == ("sale_items", "fiscal_tax_snapshot_json")
    )
    assert isinstance(snapshot_alter[1]["type_"], sa.JSON)
    assert "::json" in snapshot_alter[1]["postgresql_using"]

    rule_foreign_keys = [
        (args, kwargs)
        for event, args, kwargs in recorder.events
        if event == "create_foreign_key" and args[2] == "product_tax_rules"
    ]
    assert {args[1] for args, _kwargs in rule_foreign_keys} == {
        "products",
        "product_tax_rule_assignments",
    }
    assert all(kwargs["ondelete"] == "RESTRICT" for _args, kwargs in rule_foreign_keys)


def test_tax_rule_v2_downgrade_removes_only_v2_shape_without_data_rewrites(
    monkeypatch,
) -> None:
    migration = load_v2_migration_module()
    recorder = OperationRecorder()
    monkeypatch.setattr(migration, "op", recorder)

    migration.downgrade()

    assert recorder.executed_sql == []
    dropped_columns = {
        (args[0], args[1])
        for event, args, _kwargs in recorder.events
        if event == "drop_column"
    }
    assert dropped_columns == {
        *(('product_tax_rules', name) for name in (
            "issuer_regime",
            "destination_uf",
            "document_model",
            "cbenef",
            "tax_parameters_json",
            "approval_json",
            "published_at",
            "retired_at",
        )),
        ("sale_items", "tax_rule_id_snapshot"),
        ("fiscal_documents", "request_contract_version"),
        ("fiscal_documents", "request_payload_json"),
        ("fiscal_documents", "request_payload_sha256"),
    }


def test_tax_rule_v2_models_match_persisted_shape_without_duplicate_sources(
    monkeypatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost/test")
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    sys.path.insert(0, str(VERSIONS_PATH.parents[1] / "app"))
    models = importlib.import_module("infra.database.models")

    rule_columns = models.ProductTaxRuleModel.__table__.c
    assert {
        "issuer_regime",
        "destination_uf",
        "document_model",
        "cbenef",
        "tax_parameters_json",
        "approval_json",
        "published_at",
        "retired_at",
    } <= set(rule_columns.keys())
    assert isinstance(rule_columns.tax_parameters_json.type, sa.JSON)
    assert isinstance(rule_columns.approval_json.type, sa.JSON)

    item_columns = models.SaleItemModel.__table__.c
    assert "tax_rule_id_snapshot" in item_columns
    assert isinstance(item_columns.fiscal_tax_snapshot_json.type, sa.JSON)
    assert "fiscal_snapshot_sha256" not in item_columns
    assert (
        models.SaleItemModel.__mapper__.synonyms["fiscal_snapshot_sha256"].name
        == "snapshot_sha256"
    )

    document_columns = models.FiscalDocumentModel.__table__.c
    assert {
        "request_contract_version",
        "request_payload_json",
        "request_payload_sha256",
    } <= set(document_columns.keys())
    assert isinstance(document_columns.request_payload_json.type, sa.JSON)

    config_columns = models.FiscalTenantConfigModel.__table__.c
    assert "product_rule_enforcement" not in config_columns
    assert (
        models.FiscalTenantConfigModel.__mapper__.synonyms[
            "product_rule_enforcement"
        ].name
        == "fiscal_rule_enforcement"
    )

    product_rule_fk = next(iter(models.ProductModel.__table__.c.tax_rule_id.foreign_keys))
    assignment_rule_fk = next(
        iter(models.ProductTaxRuleAssignmentModel.__table__.c.tax_rule_id.foreign_keys)
    )
    assert product_rule_fk.ondelete == "RESTRICT"
    assert assignment_rule_fk.ondelete == "RESTRICT"
    assert models.ProductModel.tax_rule.property.back_populates == "products"
    assert (
        models.ProductTaxRuleAssignmentModel.tax_rule.property.back_populates
        == "assignments"
    )
    assert "delete" not in models.ProductTaxRuleModel.products.property.cascade
    assert "delete" not in models.ProductTaxRuleModel.assignments.property.cascade


def test_snapshot_json_adapter_preserves_postgresql_json_objects(monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost/test")
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    sys.path.insert(0, str(VERSIONS_PATH.parents[1] / "app"))
    repository = importlib.import_module("infra.repositories.sqlalchemy_repos")
    snapshot = {"tax_rule_id": "rule-1", "icms": {"cst": "00"}}

    persisted = repository._serialize_fiscal_tax_snapshot(snapshot)

    assert persisted == snapshot
    assert repository._deserialize_fiscal_tax_snapshot(persisted) == snapshot


@pytest.mark.parametrize(
    ("persisted", "expected"),
    [
        pytest.param({}, {}, id="empty-dict"),
        pytest.param([], [], id="empty-list"),
        pytest.param("", None, id="empty-string"),
        pytest.param("   ", None, id="whitespace-string"),
    ],
)
def test_snapshot_json_adapter_distinguishes_empty_containers_from_empty_strings(
    monkeypatch, persisted, expected
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost/test")
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    sys.path.insert(0, str(VERSIONS_PATH.parents[1] / "app"))
    repository = importlib.import_module("infra.repositories.sqlalchemy_repos")

    if isinstance(persisted, (dict, list)):
        assert repository._serialize_fiscal_tax_snapshot(persisted) == expected
    assert repository._deserialize_fiscal_tax_snapshot(persisted) == expected
