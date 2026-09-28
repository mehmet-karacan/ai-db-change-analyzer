// Yalnız YENİ ai-db-change-analyzer job'u için tasarım örneğidir.
// Ortam placeholder'ları kurulumda doldurulur; mevcut DB Sync dosyası değişmez.
pipeline {
    agent { label '<DEDICATED_PERSISTENT_LINUX_AGENT>' }
    options {
        skipDefaultCheckout(true)
        disableConcurrentBuilds()
        timeout(time: 30, unit: 'MINUTES')
        buildDiscarder(logRotator(daysToKeepStr: '30', numToKeepStr: '120'))
    }
    triggers {
        cron('TZ=Europe/Istanbul\n10 7,19 * * *')
    }
    environment {
        ANALYZER_CONFIG = '/etc/ai-db-change-analyzer/gpu.toml'
        ANALYZER_WHEELHOUSE = '<APPROVED_LOCAL_WHEELHOUSE_PATH>'
    }
    stages {
        stage('Checkout analyzer source') {
            steps {
                dir('analyzer-src') { checkout scm }
            }
        }
        stage('Prepare locked runtime') {
            steps {
                dir('analyzer-src') {
                    sh '''
                        set +x
                        set -eu
                        umask 077
                        python3.13 -m venv .venv
                        .venv/bin/python -m pip install \
                            --no-index --find-links "$ANALYZER_WHEELHOUSE" \
                            --require-hashes -r requirements.lock
                        PYTHONPATH="$PWD/src" .venv/bin/python -m db_change_analyzer \
                            --config "$ANALYZER_CONFIG" \
                            --emit-dir "$WORKSPACE/out/$BUILD_NUMBER" doctor --offline
                    '''
                }
            }
        }
        stage('Analyze committed DB snapshots') {
            steps {
                withCredentials([
                    usernamePassword(
                        credentialsId: '<READ_ONLY_SOURCE_GIT_CREDENTIAL_ID>',
                        usernameVariable: 'DB_ANALYZER_GIT_USERNAME',
                        passwordVariable: 'DB_ANALYZER_GIT_PASSWORD'
                    ),
                    string(
                        credentialsId: '<LITELLM_ANALYZER_KEY_CREDENTIAL_ID>',
                        variable: 'LITELLM_API_KEY'
                    ),
                    usernamePassword(
                        credentialsId: '<SMTP_CREDENTIAL_ID>',
                        usernameVariable: 'DB_ANALYZER_SMTP_USERNAME',
                        passwordVariable: 'DB_ANALYZER_SMTP_PASSWORD'
                    )
                ]) {
                    script {
                        int rc
                        dir('analyzer-src') {
                            rc = sh(returnStatus: true, script: '''
                                set +x
                                set -eu
                                umask 077
                                PYTHONPATH="$PWD/src" .venv/bin/python -m db_change_analyzer \
                                    --config "$ANALYZER_CONFIG" \
                                    --emit-dir "$WORKSPACE/out/$BUILD_NUMBER" run --allow-ai --allow-mail
                            ''')
                        }
                        if (rc == 10 || rc == 11) {
                            unstable("Analyzer sınırlı/bekleyen sonuç üretti; exit=${rc}")
                        } else if (rc != 0) {
                            error("Analyzer başarısız; exit=${rc}. result.json incelenmeli.")
                        }
                    }
                }
            }
        }
    }
    post {
        always {
            archiveArtifacts(
                artifacts: "out/${env.BUILD_NUMBER}/result.json,out/${env.BUILD_NUMBER}/report.json,out/${env.BUILD_NUMBER}/report.html,out/${env.BUILD_NUMBER}/report.txt,out/${env.BUILD_NUMBER}/delivery.json,out/${env.BUILD_NUMBER}/inventory.json",
                allowEmptyArchive: true,
                fingerprint: true
            )
        }
    }
}
