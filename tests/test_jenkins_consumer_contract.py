from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_jenkins_consumer_is_a_reviewable_single_emailext_fragment() -> None:
    source = (ROOT / "jenkins" / "consume_report_artifact.groovy").read_text(encoding="utf-8")

    assert "consumeAnalyzerReportArtifact" in source
    assert source.count("emailext(") == 1
    assert "result.artifact_ready != true" in source
    assert "result.smtp_attempts != 0" in source
    assert "result.artifact_manifest_sha256" in source
    assert "manifest.email_html_sha256" in source
    assert "mail-view.json" in source
    assert "report.report_id" in source
    assert "manifest.email_html_bytes" in source
    assert "error('Analyzer artifact" in source
    assert "def emailBody = emailHtml.replaceAll" in source
    assert "body: emailBody" in source
    assert "&#36;" in source
