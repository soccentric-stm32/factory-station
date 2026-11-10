// Jenkinsfile - multi-stage CI for HWVAL Factory (bench tool).
//
//   Checkout -> Setup -> Lint -> Type check -> CLI smoke -> Tests
//            -> Package -> Release (tags) -> Archive
//
// Package builds dist/<repo>-<version>.tar.gz (outputs, manifest.json,
// SHA256SUMS) on every run.  Release runs only for a tag v<version> and
// publishes dist/ as a GitHub release with the Jenkins secret-text credential
// github-release-token.  To release: bump the version, push, then push the
// tag (git tag v0.2.0 && git push origin v0.2.0).
//
// Pure Python; the agent needs python3 (>= 3.11), git and network access for
// PyPI. The tests run against the simulated board, so no NUCLEO-F767ZI or
// ST-LINK is required.

pipeline {
    agent any

    options {
        skipDefaultCheckout()
        timestamps()
        timeout(time: 45, unit: 'MINUTES')
        buildDiscarder(logRotator(numToKeepStr: '30', artifactNumToKeepStr: '10'))
        disableConcurrentBuilds()
    }

    parameters {
        booleanParam(name: 'PACKAGE', defaultValue: false, description: 'Build the Linux package (.deb / AppImage); always done for a release tag')
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

        stage('CLI smoke') {
            steps {
                sh '"${VENV}/bin/hwval-factory" --version'
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
            steps {
                sh '''
                    rm -rf dist
                    "${VENV}/bin/python" -m pip wheel --no-deps --wheel-dir dist .
                    "${VENV}/bin/python" -m pip freeze > build-requirements.txt
                '''
                script {
                    // The desktop bundle is slow; build it on request or for a release.
                    if (params.PACKAGE || env.TAG_NAME) {
                        sh 'PATH="${PWD}/${VENV}/bin:${PATH}" packaging/build-linux.sh'
                    }
                }
                sh '''
                    python3 ci/package.py --name factory-station --version "$(ci/version.sh)" \\
                        requirements.txt build-requirements.txt README.md LICENSE
                '''
            }
        }

        stage('Release') {
            // Only for a tag build (Multibranch job with tag discovery).  The
            // tag must be v<version>; ci/github_release.py checks it.
            when { buildingTag() }
            steps {
                withCredentials([string(credentialsId: 'github-release-token', variable: 'GITHUB_TOKEN')]) {
                    sh 'python3 ci/github_release.py --tag "${TAG_NAME}" --version "$(ci/version.sh)" dist/*'
                }
            }
        }

        stage('Archive') {
            steps {
                archiveArtifacts artifacts: 'dist/*', fingerprint: true
            }
        }
    }

    post {
        always {
            deleteDir()
        }
        success {
            echo 'HWVAL Factory: lint, tests and package checks passed.'
        }
        failure {
            echo 'HWVAL Factory: pipeline failed - see the stage logs above.'
        }
    }
}
