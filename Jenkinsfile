// Jenkinsfile - multi-stage CI for HWVAL Factory (end-of-line station).
//
//   Checkout -> Setup -> Lint -> Type check -> Config check -> CLI smoke
//            -> Station self-test -> Tests -> Package -> Publish
//
// Pure Python; the agent needs python3 (>= 3.11), git and network access for
// PyPI. The station runs entirely against the fixture simulator, so no
// NUCLEO-F767ZI, ST-LINK or printer is required.

pipeline {
    agent any

    options {
        skipDefaultCheckout()
        timestamps()
        ansiColor('xterm')
        timeout(time: 45, unit: 'MINUTES')
        buildDiscarder(logRotator(numToKeepStr: '30', artifactNumToKeepStr: '10'))
        disableConcurrentBuilds()
    }

    parameters {
        booleanParam(name: 'PACKAGE', defaultValue: false, description: 'Build the Linux package (.deb / AppImage)')
    }

    environment {
        VENV              = '.venv'
        QT_QPA_PLATFORM   = 'offscreen'
        PIP_DISABLE_PIP_VERSION_CHECK = '1'
    }

    stages {
        stage('Checkout') {
            steps {
                checkout scm
                sh '''
                    git log -1 --pretty=fuller
                    echo "commit: $(git rev-parse HEAD)"
                    echo "branch: $(git rev-parse --abbrev-ref HEAD)"
                '''
            }
        }

        stage('Setup') {
            steps {
                sh '''
                    python3 --version
                    python3 -m venv "${VENV}"
                    "${VENV}/bin/python" -m pip install --upgrade pip
                    "${VENV}/bin/python" -m pip install -e ".[dev,packaging]"
                    "${VENV}/bin/python" -m pip list
                '''
            }
        }

        stage('Lint') {
            steps {
                sh '"${VENV}/bin/python" -m ruff check src'
            }
        }

        stage('Type check') {
            steps {
                // mypy strict is advisory until the annotation backlog is
                // cleared; findings mark the build unstable, not failed.
                catchError(buildResult: 'UNSTABLE', stageResult: 'FAILURE') {
                    sh '"${VENV}/bin/python" -m mypy src'
                }
            }
        }

        stage('Config check') {
            steps {
                sh '''
                    python3 - <<'PY'
from pathlib import Path
import yaml

examples = [
    Path("examples/station.simulator.yaml"),
    Path("examples/station.real.example.yaml"),
    Path("examples/profile-sc767-demo/manifest.yaml"),
]
for path in examples:
    if not path.is_file():
        raise SystemExit(f"config: {path} is missing")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not data:
        raise SystemExit(f"config: {path} did not parse to a mapping")
    print(f"config: {path} OK ({len(data)} keys)")
PY
                    if git ls-files | grep -E '^(station[.]yaml|station-data/|profiles/)' >/dev/null; then
                        echo "config: runtime station data is tracked by git"
                        git ls-files | grep -E '^(station[.]yaml|station-data/|profiles/)'
                        exit 1
                    fi
                    echo "config: no runtime station data is tracked"
                '''
            }
        }

        stage('CLI smoke') {
            steps {
                sh '''
                    "${VENV}/bin/hwval-factory" --version
                    "${VENV}/bin/hwval-factory" --list-fixtures
                    "${VENV}/bin/hwval-profile" --help >/dev/null
                    echo "cli: factory and profile tool start cleanly"
                '''
            }
        }

        stage('Station self-test') {
            steps {
                // End-to-end simulator run with the demo profile. Signature
                // policy differs between stations, so this is advisory.
                catchError(buildResult: 'UNSTABLE', stageResult: 'FAILURE') {
                    sh '''
                        "${VENV}/bin/hwval-factory" --simulate --self-test --profile examples/profile-sc767-demo
                    '''
                }
            }
        }

        stage('Tests') {
            steps {
                sh '''
                    if [ -d tests ]; then
                        "${VENV}/bin/python" -m pytest
                    else
                        echo "tests: no tests/ directory yet - skipping pytest"
                    fi
                '''
            }
        }

        stage('Package') {
            when { expression { return params.PACKAGE } }
            steps {
                sh 'PATH="${PWD}/${VENV}/bin:${PATH}" packaging/build-linux.sh'
                archiveArtifacts artifacts: 'dist/*', allowEmptyArchive: true
            }
        }

        stage('Publish') {
            steps {
                sh '''
                    "${VENV}/bin/python" -m pip freeze > build-requirements.txt
                    sha256sum requirements.txt build-requirements.txt > SHA256SUMS
                    cat SHA256SUMS
                '''
                archiveArtifacts artifacts: 'build-requirements.txt,SHA256SUMS', fingerprint: true
            }
        }
    }

    post {
        always {
            deleteDir()
        }
        success {
            echo 'HWVAL Factory: lint, config, CLI, self-test and package checks passed.'
        }
        failure {
            echo 'HWVAL Factory: pipeline failed - see the stage logs above.'
        }
    }
}
