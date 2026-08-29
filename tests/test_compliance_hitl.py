"""
Human-in-the-Loop (HITL) Compliance Testing

Tests for interactive compliance checking, where users can:
- Confirm/reject detected PII
- Add custom compliance rules
- Override automated findings
- Create exemptions for specific data patterns
"""

from __future__ import annotations

import pytest
from dataclasses import dataclass
from typing import Any, Callable, Optional
from datetime import datetime
from backend.engine.checkpoint import UserPrompt
from backend.engine.compliance.compliance_status import (
    ColumnHipaaSummary,
    compliance_disclaimer,
)


@dataclass
class ComplianceFinding:
    """A single compliance finding that can be reviewed/overridden."""
    identifier_type: str
    column: str
    count: int
    confidence: float
    status: str = "pending"  # pending, approved, rejected, exempted
    reviewer_notes: str = ""
    reviewed_at: Optional[datetime] = None
    reviewer: Optional[str] = None


@dataclass
class ComplianceExemption:
    """Exemption for specific data patterns or columns."""
    pattern_or_column: str
    reason: str
    approved_by: str
    expires_at: Optional[datetime] = None


class InteractiveComplianceReviewer:
    """HITL interface for compliance review and override."""
    
    def __init__(self, prompt: UserPrompt):
        self.prompt = prompt
        self.findings: list[ComplianceFinding] = []
        self.exemptions: list[ComplianceExemption] = []
        self.overrides: dict[str, Any] = {}
    
    def add_finding(self, finding: ComplianceFinding) -> None:
        """Add a compliance finding for review."""
        self.findings.append(finding)
    
    def review_finding(self, finding_idx: int) -> ComplianceFinding:
        """Interactively review a single finding."""
        if finding_idx >= len(self.findings):
            raise ValueError(f"Finding {finding_idx} not found")
        
        finding = self.findings[finding_idx]
        
        # Ask user to confirm/reject
        message = f"""
Review: {finding.identifier_type} detected in '{finding.column}'
Count: {finding.count} occurrences
Confidence: {finding.confidence:.2%}

Do you approve this finding?
"""
        if self.prompt.confirm(message, {"finding": finding.__dict__}):
            finding.status = "approved"
        else:
            finding.status = "rejected"
        
        return finding
    
    def review_all_findings(self) -> dict[str, int]:
        """Review all pending findings."""
        approved = 0
        rejected = 0
        
        for idx, finding in enumerate(self.findings):
            if finding.status != "pending":
                continue
            
            self.review_finding(idx)
            if finding.status == "approved":
                approved += 1
            else:
                rejected += 1
        
        return {"approved": approved, "rejected": rejected}
    
    def create_exemption(self, pattern_or_column: str, reason: str, approved_by: str) -> ComplianceExemption:
        """Create an exemption for a specific pattern or column."""
        exemption = ComplianceExemption(
            pattern_or_column=pattern_or_column,
            reason=reason,
            approved_by=approved_by,
        )
        self.exemptions.append(exemption)
        return exemption
    
    def apply_exemptions(self, findings: list[ComplianceFinding]) -> list[ComplianceFinding]:
        """Apply exemptions to findings."""
        exempted = []
        for finding in findings:
            for exemption in self.exemptions:
                if (exemption.pattern_or_column == finding.column or
                    exemption.pattern_or_column == finding.identifier_type):
                    finding.status = "exempted"
                    exempted.append(finding)
                    break
        return exempted
    
    def override_result(self, column: str, identifier_type: str, new_count: int) -> None:
        """Override the count for a specific finding."""
        key = f"{column}:{identifier_type}"
        self.overrides[key] = new_count
    
    def get_final_compliance_status(self) -> dict[str, Any]:
        """Get final compliance status after reviews and overrides."""
        approved_findings = [f for f in self.findings if f.status == "approved"]
        rejected_findings = [f for f in self.findings if f.status == "rejected"]
        exempted_findings = [f for f in self.findings if f.status == "exempted"]
        
        # Apply overrides
        final_counts = {}
        for finding in approved_findings:
            key = f"{finding.column}:{finding.identifier_type}"
            count = self.overrides.get(key, finding.count)
            final_counts[key] = count
        
        has_phi = any(finding.count > 0 for finding in approved_findings)
        
        return {
            "has_personal_data": has_phi,
            "approved_findings": len(approved_findings),
            "rejected_findings": len(rejected_findings),
            "exempted_findings": len(exempted_findings),
            "final_counts": final_counts,
            "status": "PHI_DETECTED" if has_phi else "NO_PHI_DETECTED",
        }


class MockUserPrompt(UserPrompt):
    """Mock prompt for testing HITL workflows."""
    
    def __init__(self):
        self.confirmations: dict[str, bool] = {}
        self.default_confirm = True
    
    def confirm(self, message: str, details: dict = None) -> bool:
        """Return pre-configured confirmation or default."""
        return self.confirmations.get(message, self.default_confirm)
    
    def ask_int(self, message: str, default: int = None) -> int:
        return default if default is not None else 0
    
    def ask_text(self, message: str, default: str = None) -> str:
        return default if default is not None else ""
    
    def set_confirmation(self, message: str, value: bool) -> None:
        """Set confirmation for specific message."""
        self.confirmations[message] = value


# ============================================================================
# TESTS
# ============================================================================

class TestComplianceFinding:
    """Test ComplianceFinding dataclass."""
    
    def test_create_finding(self):
        """Should create a compliance finding."""
        finding = ComplianceFinding(
            identifier_type="EMAIL",
            column="email",
            count=5,
            confidence=0.95,
        )
        
        assert finding.identifier_type == "EMAIL"
        assert finding.column == "email"
        assert finding.count == 5
        assert finding.confidence == 0.95
        assert finding.status == "pending"
    
    def test_finding_status_update(self):
        """Should update finding status."""
        finding = ComplianceFinding(
            identifier_type="EMAIL",
            column="email",
            count=5,
            confidence=0.95,
        )
        
        finding.status = "approved"
        assert finding.status == "approved"


class TestInteractiveReviewer:
    """Test interactive compliance reviewer."""
    
    def test_create_reviewer(self):
        """Should create an interactive reviewer."""
        prompt = MockUserPrompt()
        reviewer = InteractiveComplianceReviewer(prompt)
        
        assert reviewer.prompt is prompt
        assert len(reviewer.findings) == 0
        assert len(reviewer.exemptions) == 0
    
    def test_add_finding(self):
        """Should add findings for review."""
        prompt = MockUserPrompt()
        reviewer = InteractiveComplianceReviewer(prompt)
        
        finding = ComplianceFinding(
            identifier_type="EMAIL",
            column="email",
            count=10,
            confidence=0.95,
        )
        
        reviewer.add_finding(finding)
        assert len(reviewer.findings) == 1
        assert reviewer.findings[0] == finding
    
    def test_review_finding_approval(self):
        """Should approve a finding."""
        prompt = MockUserPrompt()
        prompt.default_confirm = True
        
        reviewer = InteractiveComplianceReviewer(prompt)
        finding = ComplianceFinding(
            identifier_type="EMAIL",
            column="email",
            count=10,
            confidence=0.95,
        )
        reviewer.add_finding(finding)
        
        reviewed = reviewer.review_finding(0)
        assert reviewed.status == "approved"
    
    def test_review_finding_rejection(self):
        """Should reject a finding."""
        prompt = MockUserPrompt()
        prompt.default_confirm = False
        
        reviewer = InteractiveComplianceReviewer(prompt)
        finding = ComplianceFinding(
            identifier_type="EMAIL",
            column="email",
            count=10,
            confidence=0.95,
        )
        reviewer.add_finding(finding)
        
        reviewed = reviewer.review_finding(0)
        assert reviewed.status == "rejected"
    
    def test_review_all_findings(self):
        """Should review all pending findings."""
        prompt = MockUserPrompt()
        prompt.default_confirm = True
        
        reviewer = InteractiveComplianceReviewer(prompt)
        
        for i in range(3):
            finding = ComplianceFinding(
                identifier_type=f"TYPE{i}",
                column=f"col{i}",
                count=i + 1,
                confidence=0.9,
            )
            reviewer.add_finding(finding)
        
        counts = reviewer.review_all_findings()
        assert counts["approved"] == 3
        assert counts["rejected"] == 0
    
    def test_create_exemption(self):
        """Should create exemptions."""
        prompt = MockUserPrompt()
        reviewer = InteractiveComplianceReviewer(prompt)
        
        exemption = reviewer.create_exemption(
            pattern_or_column="test_column",
            reason="Test data",
            approved_by="admin",
        )
        
        assert exemption.pattern_or_column == "test_column"
        assert exemption.reason == "Test data"
        assert len(reviewer.exemptions) == 1
    
    def test_apply_exemptions(self):
        """Should apply exemptions to findings."""
        prompt = MockUserPrompt()
        reviewer = InteractiveComplianceReviewer(prompt)
        
        # Create exemption for a column
        reviewer.create_exemption(
            pattern_or_column="email",
            reason="Permitted use",
            approved_by="admin",
        )
        
        # Create finding for that column
        finding = ComplianceFinding(
            identifier_type="EMAIL",
            column="email",
            count=5,
            confidence=0.95,
        )
        
        exempted = reviewer.apply_exemptions([finding])
        assert len(exempted) == 1
        assert exempted[0].status == "exempted"
    
    def test_override_result(self):
        """Should override finding counts."""
        prompt = MockUserPrompt()
        reviewer = InteractiveComplianceReviewer(prompt)
        
        reviewer.override_result("email", "EMAIL", 0)
        
        assert reviewer.overrides["email:EMAIL"] == 0
    
    def test_final_compliance_status_with_findings(self):
        """Should compute final status with approved findings."""
        prompt = MockUserPrompt()
        prompt.default_confirm = True
        
        reviewer = InteractiveComplianceReviewer(prompt)
        
        finding = ComplianceFinding(
            identifier_type="EMAIL",
            column="email",
            count=5,
            confidence=0.95,
        )
        reviewer.add_finding(finding)
        reviewer.review_finding(0)
        
        status = reviewer.get_final_compliance_status()
        assert status["has_personal_data"] is True
        assert status["status"] == "PHI_DETECTED"
        assert status["approved_findings"] == 1
    
    def test_final_compliance_status_no_findings(self):
        """Should compute final status with no findings."""
        prompt = MockUserPrompt()
        prompt.default_confirm = False
        
        reviewer = InteractiveComplianceReviewer(prompt)
        
        finding = ComplianceFinding(
            identifier_type="EMAIL",
            column="email",
            count=0,
            confidence=0.0,
        )
        reviewer.add_finding(finding)
        reviewer.review_finding(0)
        
        status = reviewer.get_final_compliance_status()
        assert status["has_personal_data"] is False
        assert status["status"] == "NO_PHI_DETECTED"


class TestComplianceExemption:
    """Test exemption management."""
    
    def test_create_exemption_by_column(self):
        """Should create exemption by column name."""
        exemption = ComplianceExemption(
            pattern_or_column="user_email",
            reason="Permitted under data processing agreement",
            approved_by="data_officer",
        )
        
        assert exemption.pattern_or_column == "user_email"
        assert "data processing agreement" in exemption.reason
    
    def test_create_exemption_by_pattern(self):
        """Should create exemption by pattern."""
        exemption = ComplianceExemption(
            pattern_or_column="test_.*",
            reason="Test data only",
            approved_by="qa_lead",
        )
        
        assert exemption.pattern_or_column == "test_.*"
    
    def test_exemption_expiration(self):
        """Should support exemption expiration."""
        from datetime import datetime, timedelta
        
        future = datetime.now() + timedelta(days=30)
        exemption = ComplianceExemption(
            pattern_or_column="temporary_column",
            reason="Temporary processing",
            approved_by="admin",
            expires_at=future,
        )
        
        assert exemption.expires_at is not None
        assert exemption.expires_at > datetime.now()


class TestMockUserPrompt:
    """Test mock prompt for testing."""
    
    def test_default_confirmation(self):
        """Should return default confirmation."""
        prompt = MockUserPrompt()
        prompt.default_confirm = True
        
        assert prompt.confirm("test message") is True
    
    def test_specific_confirmation(self):
        """Should return specific confirmation."""
        prompt = MockUserPrompt()
        prompt.set_confirmation("test message", False)
        
        assert prompt.confirm("test message") is False
    
    def test_ask_int(self):
        """Should ask for integer."""
        prompt = MockUserPrompt()
        value = prompt.ask_int("Enter number", default=42)
        
        assert value == 42
    
    def test_ask_text(self):
        """Should ask for text."""
        prompt = MockUserPrompt()
        value = prompt.ask_text("Enter text", default="test")
        
        assert value == "test"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
