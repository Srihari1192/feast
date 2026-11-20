# Feast e2e test image

Precompiled Go e2e binaries for **feast-operator** and **feast-module-operator**.
CI pulls and runs the image — no Go toolchain or `go mod download` at test time.

Published as: `quay.io/feastdev-ci/feast-test-images:feast-<rhoai-version>`  
Example: `quay.io/feastdev-ci/feast-test-images:feast-3.6`  
(one RHOAI version tag per workflow run)

## Build contexts (`Dockerfile.e2e-test`)

| Context    | Path                                      |
|------------|-------------------------------------------|
| `module`   | feast-module-operator repo root           |
| `operator` | `feast/infra/feast-operator`              |
| (default)  | this directory (`run-combined.sh`)        |

```bash
docker buildx build \
  --build-context module=/path/to/feast-module-operator \
  --build-context operator=/path/to/feast/infra/feast-operator \
  -f Dockerfile.e2e-test \
  -t quay.io/feastdev-ci/feast-test-images:feast-3.6 .
```

Or use the GitHub Action `build-feast-test-images` (workflow_dispatch).

## Runtime: `FEAST_TEST`

| Value                  | Suite                                                     |
|------------------------|-----------------------------------------------------------|
| `module-operator`      | feast-module-operator foundation e2e (default)            |
| `module-upgrade-pre`   | feast-module-operator upgrade pre                         |
| `module-upgrade-post`  | feast-module-operator upgrade post                        |
| `operator-e2e`         | feast-operator `./test/e2e`                               |
| `operator-e2e-rhoai`   | feast-operator `./test/e2e_rhoai` (notebook + pre/post)   |

## Example: run against a cluster

```bash
docker run --rm \
  -e KUBECONFIG=/kubeconfig \
  -v "$KUBECONFIG:/kubeconfig:ro" \
  -e FEAST_TEST=module-operator \
  quay.io/feastdev-ci/feast-test-images:feast-3.6 -test.v

# feast-operator e2e (set RUN_ON_OPENSHIFT_CI=1 on RHOAI/OpenShift)
docker run --rm \
  -e KUBECONFIG=/kubeconfig \
  -v "$KUBECONFIG:/kubeconfig:ro" \
  -e FEAST_TEST=operator-e2e \
  -e RUN_ON_OPENSHIFT_CI=1 \
  quay.io/feastdev-ci/feast-test-images:feast-3.6 -test.v

# feast pre/post upgrade + notebooks (ginkgo focus)
docker run --rm \
  -e KUBECONFIG=/kubeconfig \
  -v "$KUBECONFIG:/kubeconfig:ro" \
  -e FEAST_TEST=operator-e2e-rhoai \
  -e RUN_ON_OPENSHIFT_CI=1 \
  quay.io/feastdev-ci/feast-test-images:feast-3.6 \
  -test.v -ginkgo.focus=feastPreUpgrade

# modular upgrade pre/post
docker run --rm \
  -e KUBECONFIG=/kubeconfig \
  -v "$KUBECONFIG:/kubeconfig:ro" \
  -e FEAST_TEST=module-upgrade-pre \
  quay.io/feastdev-ci/feast-test-images:feast-3.6 -test.v
```
