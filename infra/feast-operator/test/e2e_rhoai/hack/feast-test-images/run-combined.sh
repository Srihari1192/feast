#!/bin/bash
# Combined feast e2e runner (no Go toolchain needed at test time).
#
# FEAST_TEST:
#   module-operator       → feast-module-operator foundation e2e (default)
#   module-upgrade-pre    → feast-module-operator upgrade pre
#   module-upgrade-post   → feast-module-operator upgrade post
#   operator-e2e          → feast-operator ./test/e2e (set RUN_ON_OPENSHIFT_CI=1 on RHOAI)
#   operator-e2e-rhoai    → feast-operator ./test/e2e_rhoai (notebook + feast pre/post upgrade)
set -euo pipefail

test="${FEAST_TEST:-module-operator}"

case "$test" in
  module-operator|module)
    cd /feast-module-operator
    exec /usr/local/bin/feast-e2e "$@"
    ;;
  module-upgrade-pre|upgrade-pre)
    cd /feast-module-operator
    exec /usr/local/bin/feast-e2e-upgrade-pre "$@"
    ;;
  module-upgrade-post|upgrade-post)
    cd /feast-module-operator
    exec /usr/local/bin/feast-e2e-upgrade-post "$@"
    ;;
  operator-e2e|e2e)
    cd /feast-operator
    exec /usr/local/bin/feast-operator-e2e "$@"
    ;;
  operator-e2e-rhoai|e2e_rhoai)
    cd /feast-operator
    exec /usr/local/bin/feast-operator-e2e-rhoai "$@"
    ;;
  *)
    echo "Unknown FEAST_TEST='$test'" >&2
    echo "Use: module-operator | module-upgrade-pre | module-upgrade-post | operator-e2e | operator-e2e-rhoai" >&2
    exit 1
    ;;
esac
