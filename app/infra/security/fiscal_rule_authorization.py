"""Authorization primitives specific to accountant-approved fiscal rules."""
from __future__ import annotations

from domain.identity import UserRole


class TaxRuleApprovalEvidenceError(PermissionError):
    code = "tax_rule.approval_evidence_missing"

    def __init__(self) -> None:
        super().__init__("A publicação exige aprovação de membro contador e XML homologado.")


def assert_tax_rule_approver(role: UserRole) -> None:
    """Only an explicit accountant membership can approve or publish a rule."""
    if role is not UserRole.ACCOUNTANT:
        raise TaxRuleApprovalEvidenceError()
