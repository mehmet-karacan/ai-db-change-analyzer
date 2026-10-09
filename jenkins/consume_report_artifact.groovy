/*
 * Reviewable Jenkins Pipeline fragment.
 *
 * This is intentionally not the repository Jenkinsfile and does not create a
 * sender or a new job. Call consumeAnalyzerReportArtifact('out', subject, to)
 * from the existing job after the Analyzer process has completed.
 */
def sha256(byte[] bytes) {
    def digest = java.security.MessageDigest.getInstance('SHA-256')
    digest.digest(bytes).collect { String.format('%02x', it) }.join()
}

def consumeAnalyzerReportArtifact(String artifactDir, String subject, String recipients) {
    def result = readJSON file: "${artifactDir}/result.json"
    if (result.schema_version != '2.0' || result.outcome != 'ARTIFACT_READY' ||
        result.artifact_ready != true || result.exit_code != 0 ||
        result.delivery_mode != 'jenkins_artifact' || result.smtp_attempts != 0) {
        error("Analyzer artifact is not sendable: ${result.outcome}/${result.error_code}")
    }

    def manifest = readJSON file: "${artifactDir}/render-manifest.json"
    def report = readJSON file: "${artifactDir}/report.json"
    def mailView = readJSON file: "${artifactDir}/mail-view.json"
    if (manifest.schema_version != 'render-manifest/2.0' ||
        result.report_id != manifest.report_id ||
        result.report_id != report.report_id ||
        result.report_id != mailView.report_id ||
        result.email_html_path != 'report-email.html') {
        error('Analyzer artifact identity or version mismatch')
    }

    def manifestBytes = readFile(file: "${artifactDir}/render-manifest.json", encoding: 'UTF-8').getBytes('UTF-8')
    if (sha256(manifestBytes) != result.artifact_manifest_sha256) {
        error('Analyzer artifact manifest hash mismatch')
    }
    def emailHtml = readFile(file: "${artifactDir}/report-email.html", encoding: 'UTF-8')
    if (manifest.email_html_bytes != emailHtml.getBytes('UTF-8').length ||
        sha256(emailHtml.getBytes('UTF-8')) != manifest.email_html_sha256) {
        error('Analyzer artifact email hash mismatch')
    }

    // email-ext applies its own token expansion after this step. Preserve
    // literal model/source text such as $BUILD_URL or ${JOB_NAME} while
    // keeping the rendered mail visually unchanged.
    def emailBody = emailHtml.replaceAll(/\$(?=\{?[A-Z][A-Z0-9_]*\}?)/, '&#36;')

    emailext(
        mimeType: 'text/html; charset=UTF-8',
        body: emailBody,
        subject: subject,
        to: recipients,
    )
}
