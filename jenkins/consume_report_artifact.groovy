/*
 * Reviewable Jenkins Pipeline fragment.
 *
 * This is intentionally not the repository Jenkinsfile and does not create a
 * sender or a new job. Call consumeAnalyzerReportArtifact('out', subject, to)
 * from the existing job after the Analyzer process has completed.
 */
def sha256File(String path) {
    // Use the agent's command-line hash utility so this loaded Pipeline
    // fragment remains compatible with Jenkins' Groovy sandbox. The caller
    // supplies repository-local artifact paths (currently only `out`).
    def safePath = path.replace("'", "'\"'\"'")
    def hashLine = sh(
        script: "sha256sum -- '${safePath}'",
        returnStdout: true
    ).trim()
    return hashLine.tokenize(' ')[0]
}

def consumeAnalyzerReportArtifact(String artifactDir, String subject, String recipients) {
    def result = readJSON file: "${artifactDir}/result.json"
    if (result.schema_version != '2.0' || !(result.outcome in ['ARTIFACT_READY', 'MANUAL_COMPLETE']) ||
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

    if (sha256File("${artifactDir}/render-manifest.json") != result.artifact_manifest_sha256) {
        error('Analyzer artifact manifest hash mismatch')
    }
    def emailHtml = readFile(file: "${artifactDir}/report-email.html", encoding: 'UTF-8')
    if (manifest.email_html_bytes != emailHtml.getBytes('UTF-8').length ||
        sha256File("${artifactDir}/report-email.html") != manifest.email_html_sha256) {
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

// Pipeline `load` returns the script object only when the loaded script has a
// non-null final expression. Keep the helper methods callable from Jenkinsfile.
return this
