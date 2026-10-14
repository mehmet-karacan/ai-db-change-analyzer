// AI DB Change Analyzer job.
//
// This job is downstream of the Oracle DDL sync job. The analyzer reads only
// the committed gpu-db Git history; it never connects to Oracle. Jenkins
// archives the verified artifact bundle and sends the verified HTML report to
// the approved recipient through the existing emailext mechanism.

def analyzerUpstream = 'sky/GPU/GPU-FUSION/10-ORACLE_DB_DDL_SYNC'
def analyzerMailRecipients = 'mkaracan@innova.com.tr'
// Declarative parameters are materialized after the first Jenkins run. Keep
// the first run usable as well so it can install the job parameters/trigger.
def requestedAnalyzerConfig = (params.ANALYZER_CONFIG ?: '').trim()
def requestedAnalyzerWheelhouse = (params.ANALYZER_WHEELHOUSE ?: '').trim()
def requestedManualBaseSha = (params.ANALYZER_MANUAL_BASE_SHA ?: '').trim()
def requestedManualTargetSha = (params.ANALYZER_MANUAL_TARGET_SHA ?: '').trim()
// Runtime inputs belong to this checkout. The absolute paths were an older
// agent provisioning contract; map them to the repository copy so a normal
// GitHub checkout is sufficient.
def analyzerConfig = (!requestedAnalyzerConfig || requestedAnalyzerConfig.startsWith('/etc/ai-db-change-analyzer/'))
    ? 'config/gpu.artifact.toml'
    : requestedAnalyzerConfig
def analyzerWheelhouse = (!requestedAnalyzerWheelhouse
    || requestedAnalyzerWheelhouse.startsWith('/opt/ai-db-change-analyzer/')
    || requestedAnalyzerWheelhouse == 'vendor/wheels')
    ? 'vendor/wheels/cp310'
    : requestedAnalyzerWheelhouse
def analyzerGitCredentialId = params.ANALYZER_GIT_CREDENTIAL_ID ?: 'gpu-db-http-oracle-ddl-sync'
def analyzerModelKeyCredentialId = params.ANALYZER_MODEL_KEY_CREDENTIAL_ID ?: 'aihub-api-key'

pipeline {
    // Analyzer state and the Git cache are node-local. Keep this job on the
    // dedicated persistent agent so a retry cannot lose its checkpoint and
    // start the same model review again on another node.
    agent { label 'jenkins-2' }

    options {
        skipDefaultCheckout(true)
        disableConcurrentBuilds()
        timeout(time: 30, unit: 'MINUTES')
        buildDiscarder(logRotator(daysToKeepStr: '30', numToKeepStr: '120'))
    }

    // The first successful run of this job installs the trigger in Jenkins.
    // The upstream job may finish successfully with NO_CHANGE; the analyzer's
    // Git range/checkpoint and base_sha+target_sha guards then produce NO_CHANGE
    // without invoking the model or creating a duplicate report.
    triggers {
        upstream(upstreamProjects: analyzerUpstream, threshold: hudson.model.Result.SUCCESS)
    }

    parameters {
        string(
            name: 'ANALYZER_CONFIG',
            defaultValue: 'config/gpu.artifact.toml',
            description: 'Analyzer repository içindeki delivery.mode=jenkins_artifact olan GPU config yolu.'
        )
        string(
            name: 'ANALYZER_WHEELHOUSE',
            defaultValue: 'vendor/wheels/cp310',
            description: 'Analyzer repository içindeki onaylı, hash kilitli Python wheel klasörü.'
        )
        string(
            name: 'ANALYZER_MANUAL_BASE_SHA',
            defaultValue: '',
            description: 'Opsiyonel manuel test aralığı başlangıç SHA değeri. Boş bırakılırsa otomatik checkpoint kullanılır.'
        )
        string(
            name: 'ANALYZER_MANUAL_TARGET_SHA',
            defaultValue: '',
            description: 'Opsiyonel manuel test aralığı hedef SHA değeri. Base ile birlikte verilirse mail üreten manuel analiz çalışır.'
        )
        credentials(
            name: 'ANALYZER_GIT_CREDENTIAL_ID',
            defaultValue: 'gpu-db-http-oracle-ddl-sync',
            description: 'gpu-db master için salt-okunur veya mevcut kontrollü Bitbucket credential.',
            credentialType: 'com.cloudbees.plugins.credentials.impl.UsernamePasswordCredentialsImpl',
            required: true
        )
        credentials(
            name: 'ANALYZER_MODEL_KEY_CREDENTIAL_ID',
            defaultValue: 'aihub-api-key',
            description: 'Kurumsal model endpoint anahtarı (Secret text).',
            credentialType: 'org.jenkinsci.plugins.plaincredentials.impl.StringCredentialsImpl',
            required: true
        )
    }

    stages {
        stage('Checkout analyzer source') {
            steps {
                dir('analyzer-src') {
                    checkout scm
                }
            }
        }

        stage('Prepare locked runtime') {
            steps {
                dir('analyzer-src') {
                    withEnv([
                        "ANALYZER_CONFIG=${analyzerConfig}",
                        "ANALYZER_WHEELHOUSE=${analyzerWheelhouse}"
                    ]) {
                        sh '''
                        set +x
                        set -eu
                        umask 077
                        test -d "$ANALYZER_WHEELHOUSE" || {
                            echo "ANALYZER_WHEELHOUSE bulunamadı: $ANALYZER_WHEELHOUSE" >&2
                            exit 2
                        }
                        command -v python3 >/dev/null 2>&1 || {
                            echo "Jenkins agent üzerinde python3 bulunamadı." >&2
                            exit 2
                        }
                        python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else "Python 3.10+ gerekli")'
                        rm -rf .site
                        mkdir -p .site
                        for wheel in "$ANALYZER_WHEELHOUSE"/*.whl; do
                            python3 -m zipfile -e "$wheel" .site
                        done
                        python3 tools/prepare_runtime_config.py \
                            "$ANALYZER_CONFIG" "$WORKSPACE/.analyzer-runtime.toml"
                        if [ -L .analyzer-state ]; then
                            echo 'Analyzer state klasörü symlink olamaz.' >&2
                            exit 2
                        fi
                        if [ -e .analyzer-state ] && [ ! -f .analyzer-state/INSTALLATION.json ]; then
                            mv .analyzer-state "$WORKSPACE/.analyzer-state-orphaned-$BUILD_NUMBER"
                        fi
                        if [ ! -f .analyzer-state/INSTALLATION.json ]; then
                            printf '1\n' > "$WORKSPACE/.analyzer-first-run"
                            PYTHONPATH="$PWD/.site:$PWD/src" python3 -m db_change_analyzer \
                                --config "$WORKSPACE/.analyzer-runtime.toml" \
                                state init --confirm-new-install
                        else
                            printf '0\n' > "$WORKSPACE/.analyzer-first-run"
                        fi
                        PYTHONPATH="$PWD/.site:$PWD/src" python3 -m db_change_analyzer \
                            --config "$WORKSPACE/.analyzer-runtime.toml" \
                            doctor --offline
                        '''
                    }
                }
            }
        }

        stage('Analyze committed DB snapshots') {
            steps {
                withCredentials([
                    usernamePassword(
                        credentialsId: analyzerGitCredentialId,
                        usernameVariable: 'DB_ANALYZER_GIT_USERNAME',
                        passwordVariable: 'DB_ANALYZER_GIT_PASSWORD'
                    ),
                    string(
                        credentialsId: analyzerModelKeyCredentialId,
                        variable: 'LITELLM_API_KEY'
                    )
                ]) {
                    script {
                        int rc
                        dir('analyzer-src') {
                            withEnv([
                                "ANALYZER_CONFIG=${env.WORKSPACE}/.analyzer-runtime.toml",
                                "ANALYZER_MANUAL_BASE_SHA=${requestedManualBaseSha}",
                                "ANALYZER_MANUAL_TARGET_SHA=${requestedManualTargetSha}"
                            ]) {
                                rc = sh(
                                    returnStatus: true,
                                    script: '''
                                    set +x
                                    set -eu
                                    umask 077
                                    rm -rf "$WORKSPACE/out"
                                    mkdir -p "$WORKSPACE/out"
                                    echo 'ANALYZER_PHASE=SMOKE_MODEL_START'
                                    if [ ! -f "$PWD/.analyzer-state/capability-record.json" ]; then
                                        PYTHONPATH="$PWD/.site:$PWD/src" python3 -m db_change_analyzer \
                                            --config "$ANALYZER_CONFIG" \
                                            --emit-dir "$WORKSPACE/out" \
                                            smoke-model --allow-ai --record "$WORKSPACE/out/capability-record.json"
                                        cp "$WORKSPACE/out/capability-record.json" "$PWD/.analyzer-state/capability-record.json"
                                    fi
                                    echo 'ANALYZER_PHASE=SMOKE_MODEL_COMPLETE'
                                    if [ "$(cat "$WORKSPACE/.analyzer-first-run")" = '1' ]; then
                                        set -- $(PYTHONPATH="$PWD/.site:$PWD/src" python3 \
                                            tools/resolve_initial_range.py "$ANALYZER_CONFIG")
                                        if [ "$1" = 'ROOT' ]; then
                                            echo 'İlk analiz için karşılaştırılabilir bir önceki commit bulunamadı.' >&2
                                            exit 12
                                        fi
                                        PYTHONPATH="$PWD/.site:$PWD/src" python3 -m db_change_analyzer \
                                            --config "$ANALYZER_CONFIG" \
                                            state rebaseline --expected-base ROOT --target "$1" \
                                            --reason 'İlk Jenkins analizinde son committen önceki commit checkpoint olarak alındı.' \
                                            --ack-unanalysed-history
                                    fi
                                    echo 'ANALYZER_PHASE=RUN_START'
                                    manual_base_sha="${ANALYZER_MANUAL_BASE_SHA:-}"
                                    manual_target_sha="${ANALYZER_MANUAL_TARGET_SHA:-}"
                                    if [ -n "$manual_base_sha" ] || [ -n "$manual_target_sha" ]; then
                                        if [ -z "$manual_base_sha" ] || [ -z "$manual_target_sha" ]; then
                                            echo 'Manuel analiz için base ve target SHA birlikte verilmelidir.' >&2
                                            exit 2
                                        fi
                                        echo 'ANALYZER_PHASE=MANUAL_RUN_START'
                                        PYTHONPATH="$PWD/.site:$PWD/src" python3 -m db_change_analyzer \
                                            --config "$ANALYZER_CONFIG" \
                                            --emit-dir "$WORKSPACE/out" \
                                            manual --base "$manual_base_sha" \
                                            --target "$manual_target_sha" --allow-ai
                                    else
                                        PYTHONPATH="$PWD/.site:$PWD/src" python3 -m db_change_analyzer \
                                            --config "$ANALYZER_CONFIG" \
                                            --emit-dir "$WORKSPACE/out" \
                                            run --allow-ai
                                    fi
                                    echo 'ANALYZER_PHASE=RUN_COMPLETE'
                                    '''
                                )
                            }
                        }
                        if (rc == 10 || rc == 11) {
                            unstable("Analyzer sınırlı veya bekleyen sonuç üretti; exit=${rc}. result.json incelenmeli.")
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
                artifacts: 'out/**',
                allowEmptyArchive: true,
                fingerprint: true
            )

            script {
                if (fileExists('out/result.json')) {
                    def result = readJSON file: 'out/result.json'
                    if (result.outcome in ['ARTIFACT_READY', 'MANUAL_COMPLETE']) {
                        def consumer = load 'analyzer-src/jenkins/consume_report_artifact.groovy'
                        consumer.consumeAnalyzerReportArtifact(
                            'out',
                            "GPU | AI DB Change Analyzer | ${currentBuild.currentResult ?: 'UNKNOWN'} | Build #${env.BUILD_NUMBER}",
                            analyzerMailRecipients
                        )
                    }
                }
            }
        }
    }
}
