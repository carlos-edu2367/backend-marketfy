import ast
from collections import Counter
import importlib.util
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "20260714_0002_product_tax_rule_assignment_history.py"
)
VERSIONS_PATH = MIGRATION_PATH.parent


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


class OperationRecorder:
    def __init__(self):
        self.executed_sql = []

    def create_table(self, *args, **kwargs):
        return None

    def create_index(self, *args, **kwargs):
        return None

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

    assert len(script.get_heads()) == 1
