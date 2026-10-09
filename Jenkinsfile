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
    agent any

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
                                "ANALYZER_CONFIG=${env.WORKSPACE}/.analyzer-runtime.toml"
                            ]) {
                                rc = sh(
                                    returnStatus: true,
                                    script: '''
                                    set +x
                                    set -eu
                                    umask 077
                                    rm -rf "$WORKSPACE/out"
                                    mkdir -p "$WORKSPACE/out"
                                    model_probe_body="$WORKSPACE/out/model-probe-response.json"
                                    model_http_status="$(curl --silent --show-error --output "$model_probe_body" --write-out '%{http_code}' \
                                        --connect-timeout 10 --max-time 60 \
                                        --cacert "$PWD/config/certs/turktelekom-sub-g3-01.pem" \
                                        --header "Authorization: Bearer $LITELLM_API_KEY" \
                                        --header 'Content-Type: application/json' \
                                        --data '{"model":"Qwen/Qwen3.8-27B-FP8","messages":[{"role":"user","content":"Return only the word OK"}],"stream":false,"max_tokens":16}' \
                                        'https://aihub-api.turktelekom.com.tr/chat/completions' || true)"
                                    echo "MODEL_VALID_ID_SIMPLE_HTTP=$model_http_status"
                                    if [ "$model_http_status" != '200' ]; then
                                        model_probe_message="$(tr '\\n' ' ' < "$model_probe_body" | cut -c1-1000)"
                                        echo "MODEL_VALID_ID_SIMPLE_BODY=$model_probe_message"
                                    fi
                                    rm -f "$model_probe_body"
                                    model_schema_payload="$WORKSPACE/out/model-schema-payload.json"
                                    cat > "$model_schema_payload" <<'JSON'
{"model":"Qwen/Qwen3.8-27B-FP8","messages":[{"role":"system","content":"Return a JSON object."},{"role":"user","content":"Return an object with ok=true."}],"stream":false,"max_tokens":64,"response_format":{"type":"json_schema","json_schema":{"name":"smoke","strict":true,"schema":{"type":"object","properties":{"ok":{"type":"boolean"}},"required":["ok"],"additionalProperties":false}}},"chat_template_kwargs":{"enable_thinking":false}}
JSON
                                    model_schema_body="$WORKSPACE/out/model-schema-response.json"
                                    model_schema_http_status="$(curl --silent --show-error --output "$model_schema_body" --write-out '%{http_code}' \
                                        --connect-timeout 10 --max-time 60 \
                                        --cacert "$PWD/config/certs/turktelekom-sub-g3-01.pem" \
                                        --header "Authorization: Bearer $LITELLM_API_KEY" \
                                        --header 'Content-Type: application/json' \
                                        --data-binary "@$model_schema_payload" \
                                        'https://aihub-api.turktelekom.com.tr/chat/completions' || true)"
                                    echo "MODEL_JSON_SCHEMA_HTTP=$model_schema_http_status"
                                    if [ "$model_schema_http_status" != '200' ]; then
                                        model_schema_message="$(tr '\\n' ' ' < "$model_schema_body" | cut -c1-1000)"
                                        echo "MODEL_JSON_SCHEMA_BODY=$model_schema_message"
                                    fi
                                    rm -f "$model_schema_payload" "$model_schema_body"
                                    model_tools_payload="$WORKSPACE/out/model-tools-payload.json"
                                    cat > "$model_tools_payload" <<'JSON'
{"model":"Qwen/Qwen3.8-27B-FP8","messages":[{"role":"system","content":"Use synthetic_lookup exactly once, then return JSON."},{"role":"user","content":"Call synthetic_lookup with nonce probe-nonce-1."}],"tools":[{"type":"function","function":{"name":"synthetic_lookup","description":"fixed synthetic probe","parameters":{"type":"object","properties":{"nonce":{"type":"string"}},"required":["nonce"],"additionalProperties":false}}}],"tool_choice":"auto","stream":false,"max_tokens":128,"response_format":{"type":"json_schema","json_schema":{"name":"smoke","strict":true,"schema":{"type":"object","properties":{"ok":{"type":"boolean"}},"required":["ok"],"additionalProperties":false}}},"chat_template_kwargs":{"enable_thinking":false}}
JSON
                                    model_tools_body="$WORKSPACE/out/model-tools-response.json"
                                    model_tools_http_status="$(curl --silent --show-error --output "$model_tools_body" --write-out '%{http_code}' \
                                        --connect-timeout 10 --max-time 60 \
                                        --cacert "$PWD/config/certs/turktelekom-sub-g3-01.pem" \
                                        --header "Authorization: Bearer $LITELLM_API_KEY" \
                                        --header 'Content-Type: application/json' \
                                        --data-binary "@$model_tools_payload" \
                                        'https://aihub-api.turktelekom.com.tr/chat/completions' || true)"
                                    echo "MODEL_TOOLS_HTTP=$model_tools_http_status"
                                    if [ "$model_tools_http_status" != '200' ]; then
                                        model_tools_message="$(tr '\\n' ' ' < "$model_tools_body" | cut -c1-1000)"
                                        echo "MODEL_TOOLS_BODY=$model_tools_message"
                                    fi
                                    rm -f "$model_tools_payload" "$model_tools_body"
                                    if [ ! -f "$WORKSPACE/.analyzer-state/capability-record.json" ]; then
                                        PYTHONPATH="$PWD/.site:$PWD/src" python3 -m db_change_analyzer \
                                            --config "$ANALYZER_CONFIG" \
                                            --emit-dir "$WORKSPACE/out" \
                                            smoke-model --allow-ai --record "$WORKSPACE/out/capability-record.json"
                                        cp "$WORKSPACE/out/capability-record.json" "$WORKSPACE/.analyzer-state/capability-record.json"
                                    fi
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
                                    PYTHONPATH="$PWD/.site:$PWD/src" python3 -m db_change_analyzer \
                                        --config "$ANALYZER_CONFIG" \
                                        --emit-dir "$WORKSPACE/out" \
                                        run --allow-ai
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
                    if (result.outcome == 'ARTIFACT_READY') {
                        def consumer = load 'analyzer-src/jenkins/consume_report_artifact.groovy'
                        consumer.consumeAnalyzerReportArtifact(
                            'out',
                            "AI DB Değişiklik Analizi | ${env.JOB_NAME} #${env.BUILD_NUMBER}",
                            analyzerMailRecipients
                        )
                    }
                }
            }
        }
    }
}
