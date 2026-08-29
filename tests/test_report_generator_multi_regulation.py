"""
Multi-Regulation Compliance Report Generator Tests

Tests for generating comprehensive compliance reports across:
- GDPR (EU)
- HIPAA (USA)
- CCPA (California)
- PCI-DSS (Payment Card Industry)
- GLBA (Financial Privacy)
"""

from __future__ import annotations

import pytest
from dataclasses import dataclass, field
from typing import Any, Optional
from enum import Enum


class RegulationType(Enum):
    """Supported compliance regulations."""
    GDPR = "GDPR"
    HIPAA = "HIPAA"
    CCPA = "CCPA"
    PCI_DSS = "PCI_DSS"
    GLBA = "GLBA"


class ComplianceStatus(Enum):
    """Compliance status levels."""
    COMPLIANT = "COMPLIANT"
    NON_COMPLIANT = "NON_COMPLIANT"
    PARTIALLY_COMPLIANT = "PARTIALLY_COMPLIANT"
    UNKNOWN = "UNKNOWN"


@dataclass
class RegulationComplianceReport:
    """Single regulation compliance report."""
    regulation: RegulationType
    status: ComplianceStatus
    findings: list[str] = field(default_factory=list)
    data_subjects: int = 0
    personal_data_detected: bool = False
    sensitive_data_detected: bool = False
    recommendations: list[str] = field(default_factory=list)
    risk_level: str = "LOW"  # LOW, MEDIUM, HIGH, CRITICAL
    remediation_actions: list[str] = field(default_factory=list)
    assessment_date: Optional[str] = None
    next_review_date: Optional[str] = None


@dataclass
class MultiRegulationComplianceReport:
    """Comprehensive compliance report across multiple regulations."""
    dataset_name: str
    regulations: dict[RegulationType, RegulationComplianceReport] = field(default_factory=dict)
    overall_status: ComplianceStatus = ComplianceStatus.UNKNOWN
    overall_risk: str = "UNKNOWN"
    total_findings: int = 0
    data_sensitivity_score: float = 0.0  # 0-1
    summary_text: str = ""
    action_items: list[str] = field(default_factory=list)
    generated_at: Optional[str] = None


class MultiRegulationReportGenerator:
    """Generate compliance reports across multiple regulations."""
    
    def __init__(self, dataset_name: str):
        self.dataset_name = dataset_name
        self.report = MultiRegulationComplianceReport(dataset_name=dataset_name)
        self.detections: dict[RegulationType, Any] = {}
    
    def add_regulation_findings(
        self,
        regulation: RegulationType,
        findings: dict[str, Any],
    ) -> None:
        """Add findings for a specific regulation."""
        self.detections[regulation] = findings
    
    def generate_gdpr_report(self) -> RegulationComplianceReport:
        """Generate GDPR compliance report."""
        findings = self.detections.get(RegulationType.GDPR, {})
        
        has_personal_data = findings.get("personal_data_detected", False)
        has_sensitive = findings.get("sensitive_categories", False)
        
        status = (
            ComplianceStatus.NON_COMPLIANT if has_personal_data
            else ComplianceStatus.COMPLIANT
        )
        
        report = RegulationComplianceReport(
            regulation=RegulationType.GDPR,
            status=status,
            personal_data_detected=has_personal_data,
            sensitive_data_detected=has_sensitive,
            risk_level="HIGH" if has_personal_data else "LOW",
        )
        
        if has_personal_data:
            report.findings.append("Personal data detected in dataset")
            report.recommendations.append("Implement data minimization")
            report.recommendations.append("Document legal basis for processing")
        
        if has_sensitive:
            report.findings.append("Special category data (sensitive) detected")
            report.risk_level = "CRITICAL"
            report.recommendations.append("Implement strict access controls")
            report.recommendations.append("Document explicit consent mechanisms")
        
        return report
    
    def generate_hipaa_report(self) -> RegulationComplianceReport:
        """Generate HIPAA compliance report."""
        findings = self.detections.get(RegulationType.HIPAA, {})
        
        has_phi = findings.get("phi_detected", False)
        
        status = (
            ComplianceStatus.NON_COMPLIANT if has_phi
            else ComplianceStatus.COMPLIANT
        )
        
        report = RegulationComplianceReport(
            regulation=RegulationType.HIPAA,
            status=status,
            personal_data_detected=has_phi,
            risk_level="CRITICAL" if has_phi else "LOW",
        )
        
        if has_phi:
            report.findings.append("Protected Health Information (PHI) detected")
            report.recommendations.append("Implement HIPAA-compliant access controls")
            report.recommendations.append("Establish Business Associate Agreements")
            report.recommendations.append("Conduct risk assessment")
        
        return report
    
    def generate_ccpa_report(self) -> RegulationComplianceReport:
        """Generate CCPA compliance report."""
        findings = self.detections.get(RegulationType.CCPA, {})
        
        has_personal_info = findings.get("personal_information_detected", False)
        
        status = (
            ComplianceStatus.PARTIALLY_COMPLIANT if has_personal_info
            else ComplianceStatus.COMPLIANT
        )
        
        report = RegulationComplianceReport(
            regulation=RegulationType.CCPA,
            status=status,
            personal_data_detected=has_personal_info,
            risk_level="MEDIUM" if has_personal_info else "LOW",
        )
        
        if has_personal_info:
            report.findings.append("California personal information detected")
            report.recommendations.append("Implement consumer rights mechanisms")
            report.recommendations.append("Establish privacy notice updates")
            report.recommendations.append("Document data retention policies")
        
        return report
    
    def generate_pci_dss_report(self) -> RegulationComplianceReport:
        """Generate PCI DSS compliance report."""
        findings = self.detections.get(RegulationType.PCI_DSS, {})
        
        has_card_data = findings.get("card_data_detected", False)
        
        status = (
            ComplianceStatus.NON_COMPLIANT if has_card_data
            else ComplianceStatus.COMPLIANT
        )
        
        report = RegulationComplianceReport(
            regulation=RegulationType.PCI_DSS,
            status=status,
            personal_data_detected=has_card_data,
            risk_level="CRITICAL" if has_card_data else "LOW",
        )
        
        if has_card_data:
            report.findings.append("Payment card data detected")
            report.recommendations.append("Implement PCI DSS compliant infrastructure")
            report.recommendations.append("Conduct quarterly security assessments")
            report.recommendations.append("Maintain secure encryption standards")
        
        return report
    
    def generate_glba_report(self) -> RegulationComplianceReport:
        """Generate GLBA compliance report."""
        findings = self.detections.get(RegulationType.GLBA, {})
        
        has_financial_data = findings.get("financial_data_detected", False)
        has_nonpublic_info = findings.get("nonpublic_information", False)
        
        status = (
            ComplianceStatus.NON_COMPLIANT if (has_financial_data or has_nonpublic_info)
            else ComplianceStatus.COMPLIANT
        )
        
        report = RegulationComplianceReport(
            regulation=RegulationType.GLBA,
            status=status,
            personal_data_detected=has_financial_data,
            risk_level="HIGH" if (has_financial_data or has_nonpublic_info) else "LOW",
        )
        
        if has_financial_data or has_nonpublic_info:
            report.findings.append("Nonpublic financial information detected")
            report.recommendations.append("Implement GLBA safeguards rules")
            report.recommendations.append("Establish customer notification procedures")
            report.recommendations.append("Conduct privacy impact assessments")
        
        return report
    
    def generate_all_reports(self) -> MultiRegulationComplianceReport:
        """Generate reports for all applicable regulations."""
        all_reports = {
            RegulationType.GDPR: self.generate_gdpr_report(),
            RegulationType.HIPAA: self.generate_hipaa_report(),
            RegulationType.CCPA: self.generate_ccpa_report(),
            RegulationType.PCI_DSS: self.generate_pci_dss_report(),
            RegulationType.GLBA: self.generate_glba_report(),
        }
        
        self.report.regulations = all_reports
        
        # Calculate overall status
        statuses = [r.status for r in all_reports.values()]
        if any(s == ComplianceStatus.NON_COMPLIANT for s in statuses):
            self.report.overall_status = ComplianceStatus.NON_COMPLIANT
        elif any(s == ComplianceStatus.PARTIALLY_COMPLIANT for s in statuses):
            self.report.overall_status = ComplianceStatus.PARTIALLY_COMPLIANT
        else:
            self.report.overall_status = ComplianceStatus.COMPLIANT
        
        # Calculate overall risk
        risk_levels = [r.risk_level for r in all_reports.values()]
        if "CRITICAL" in risk_levels:
            self.report.overall_risk = "CRITICAL"
        elif "HIGH" in risk_levels:
            self.report.overall_risk = "HIGH"
        elif "MEDIUM" in risk_levels:
            self.report.overall_risk = "MEDIUM"
        else:
            self.report.overall_risk = "LOW"
        
        # Aggregate findings
        total_findings = sum(len(r.findings) for r in all_reports.values())
        self.report.total_findings = total_findings
        
        # Calculate data sensitivity score
        critical_count = sum(1 for r in all_reports.values() if r.risk_level == "CRITICAL")
        high_count = sum(1 for r in all_reports.values() if r.risk_level == "HIGH")
        self.report.data_sensitivity_score = min(1.0, (critical_count * 0.25 + high_count * 0.15) / 5)
        
        return self.report
    
    def generate_summary_text(self) -> str:
        """Generate human-readable summary."""
        if not self.report.regulations:
            return "No compliance assessment conducted."
        
        summary = f"Compliance Assessment for: {self.dataset_name}\n"
        summary += f"Overall Status: {self.report.overall_status.value}\n"
        summary += f"Overall Risk Level: {self.report.overall_risk}\n"
        summary += f"Total Findings: {self.report.total_findings}\n"
        summary += f"Data Sensitivity Score: {self.report.data_sensitivity_score:.2%}\n\n"
        
        for regulation, reg_report in self.report.regulations.items():
            summary += f"\n{regulation.value}:\n"
            summary += f"  Status: {reg_report.status.value}\n"
            summary += f"  Risk Level: {reg_report.risk_level}\n"
            if reg_report.findings:
                summary += "  Findings:\n"
                for finding in reg_report.findings:
                    summary += f"    - {finding}\n"
        
        self.report.summary_text = summary
        return summary


# ============================================================================
# TESTS
# ============================================================================

class TestRegulationComplianceReport:
    """Test individual regulation reports."""
    
    def test_create_gdpr_report(self):
        """Should create GDPR compliance report."""
        report = RegulationComplianceReport(
            regulation=RegulationType.GDPR,
            status=ComplianceStatus.COMPLIANT,
        )
        
        assert report.regulation == RegulationType.GDPR
        assert report.status == ComplianceStatus.COMPLIANT
    
    def test_report_with_findings(self):
        """Report should support findings."""
        report = RegulationComplianceReport(
            regulation=RegulationType.HIPAA,
            status=ComplianceStatus.NON_COMPLIANT,
        )
        
        report.findings.append("PHI detected in column X")
        report.findings.append("Encryption not implemented")
        
        assert len(report.findings) == 2
    
    def test_report_with_recommendations(self):
        """Report should support recommendations."""
        report = RegulationComplianceReport(
            regulation=RegulationType.PCI_DSS,
            status=ComplianceStatus.NON_COMPLIANT,
        )
        
        report.recommendations.append("Implement encryption")
        report.recommendations.append("Update security policies")
        
        assert len(report.recommendations) == 2


class TestMultiRegulationReport:
    """Test multi-regulation compliance report."""
    
    def test_create_multi_regulation_report(self):
        """Should create multi-regulation report."""
        report = MultiRegulationComplianceReport(dataset_name="test_dataset")
        
        assert report.dataset_name == "test_dataset"
        assert report.overall_status == ComplianceStatus.UNKNOWN
        assert len(report.regulations) == 0
    
    def test_report_with_multiple_regulations(self):
        """Should support multiple regulations."""
        report = MultiRegulationComplianceReport(dataset_name="test")
        
        for reg_type in [RegulationType.GDPR, RegulationType.HIPAA, RegulationType.PCI_DSS]:
            reg_report = RegulationComplianceReport(
                regulation=reg_type,
                status=ComplianceStatus.COMPLIANT,
            )
            report.regulations[reg_type] = reg_report
        
        assert len(report.regulations) == 3


class TestMultiRegulationGenerator:
    """Test multi-regulation report generator."""
    
    def test_create_generator(self):
        """Should create report generator."""
        gen = MultiRegulationReportGenerator("test_dataset")
        
        assert gen.dataset_name == "test_dataset"
        assert len(gen.detections) == 0
    
    def test_add_findings(self):
        """Should add regulation findings."""
        gen = MultiRegulationReportGenerator("test")
        
        gen.add_regulation_findings(
            RegulationType.GDPR,
            {"personal_data_detected": True}
        )
        
        assert RegulationType.GDPR in gen.detections
        assert gen.detections[RegulationType.GDPR]["personal_data_detected"] is True
    
    def test_generate_gdpr_report(self):
        """Should generate GDPR report."""
        gen = MultiRegulationReportGenerator("test")
        gen.add_regulation_findings(
            RegulationType.GDPR,
            {"personal_data_detected": True}
        )
        
        report = gen.generate_gdpr_report()
        
        assert report.regulation == RegulationType.GDPR
        assert report.status == ComplianceStatus.NON_COMPLIANT
        assert len(report.findings) > 0
    
    def test_generate_hipaa_report(self):
        """Should generate HIPAA report."""
        gen = MultiRegulationReportGenerator("test")
        gen.add_regulation_findings(
            RegulationType.HIPAA,
            {"phi_detected": False}
        )
        
        report = gen.generate_hipaa_report()
        
        assert report.regulation == RegulationType.HIPAA
        assert report.status == ComplianceStatus.COMPLIANT
    
    def test_generate_all_reports(self):
        """Should generate all regulation reports."""
        gen = MultiRegulationReportGenerator("test_dataset")
        
        gen.add_regulation_findings(
            RegulationType.GDPR,
            {"personal_data_detected": True}
        )
        gen.add_regulation_findings(
            RegulationType.HIPAA,
            {"phi_detected": False}
        )
        
        report = gen.generate_all_reports()
        
        assert len(report.regulations) >= 2
        assert RegulationType.GDPR in report.regulations
        assert RegulationType.HIPAA in report.regulations
    
    def test_overall_status_calculation(self):
        """Should calculate correct overall status."""
        gen = MultiRegulationReportGenerator("test")
        
        # Mix of compliant and non-compliant
        gen.add_regulation_findings(
            RegulationType.GDPR,
            {"personal_data_detected": True}
        )
        gen.add_regulation_findings(
            RegulationType.HIPAA,
            {"phi_detected": False}
        )
        
        report = gen.generate_all_reports()
        
        # Should be non-compliant if any regulation is non-compliant
        assert report.overall_status in [
            ComplianceStatus.NON_COMPLIANT,
            ComplianceStatus.PARTIALLY_COMPLIANT
        ]
    
    def test_overall_risk_calculation(self):
        """Should calculate correct overall risk."""
        gen = MultiRegulationReportGenerator("test")
        
        gen.add_regulation_findings(
            RegulationType.PCI_DSS,
            {"card_data_detected": True}
        )
        
        report = gen.generate_all_reports()
        
        # PCI DSS with card data should be CRITICAL
        assert report.overall_risk in ["CRITICAL", "HIGH"]
    
    def test_generate_summary_text(self):
        """Should generate human-readable summary."""
        gen = MultiRegulationReportGenerator("test_dataset")
        
        gen.add_regulation_findings(
            RegulationType.GDPR,
            {"personal_data_detected": True}
        )
        gen.generate_all_reports()
        
        summary = gen.generate_summary_text()
        
        assert "test_dataset" in summary
        assert "GDPR" in summary
        assert "Status" in summary


class TestComplianceRiskAssessment:
    """Test compliance risk assessment."""
    
    def test_critical_risk_detection(self):
        """Should detect CRITICAL risk level."""
        gen = MultiRegulationReportGenerator("test")
        
        gen.add_regulation_findings(
            RegulationType.PCI_DSS,
            {"card_data_detected": True}
        )
        gen.add_regulation_findings(
            RegulationType.HIPAA,
            {"phi_detected": True}
        )
        
        report = gen.generate_all_reports()
        assert report.overall_risk == "CRITICAL"
    
    def test_sensitivity_score_calculation(self):
        """Should calculate data sensitivity score."""
        gen = MultiRegulationReportGenerator("test")
        
        gen.add_regulation_findings(
            RegulationType.GDPR,
            {"personal_data_detected": True, "sensitive_categories": True}
        )
        
        report = gen.generate_all_reports()
        
        # Score should be between 0 and 1
        assert 0.0 <= report.data_sensitivity_score <= 1.0


class TestRegulationTypes:
    """Test regulation type enumerations."""
    
    def test_all_regulation_types_defined(self):
        """Should have all major regulation types."""
        regulations = [reg.value for reg in RegulationType]
        
        assert "GDPR" in regulations
        assert "HIPAA" in regulations
        assert "CCPA" in regulations
        assert "PCI_DSS" in regulations
        assert "GLBA" in regulations


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
