/*
Copyright 2025 Feast Community.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
*/

package e2erhoai

// Modular architecture upgrade tests
//
// These tests validate the transition from the in-tree Feast reconciler (Version N)
// to the module controller (Version N+1) in RHOAI's modular architecture:
//
//   Version N:
//     - ODH operator has an in-tree Feast component reconciler
//     - feast-operator Deployment is running (deployed by in-tree reconciler)
//     - FeatureStore CRs are being reconciled, operands are running
//
//   Version N+1 (after migration):
//     - ODH operator upgrade installs new binary
//     - In-tree Feast reconciler no longer exists
//     - Module controller takes over:
//         1. Renders Helm chart → same feast-operator Deployment spec
//         2. SSA applies Deployment (no-op or update if image changed)
//         3. feast-operator continues running (no restart if spec unchanged)
//         4. Creates/adopts FeastOperator component CR
//     - FeatureStore CRs unaffected — feast-operator never stopped
//
// Two test pairs run in sequence controlled by TEST_TIER env var:
//
//   Pre-Upgrade:
//     - Snapshot feast-operator Deployment generation + spec
//     - Snapshot each FeatureStore CR generation + spec
//     - Store ODH version from DSCI
//
//   Post-Upgrade:
//     - Verify feast-operator Deployment spec unchanged (no unexpected restart)
//     - Verify FeatureStore CR specs unchanged (operator did not mutate on first reconcile)
//     - Verify operand pods had zero restarts (serving was uninterrupted)
//     - Verify FeatureStore CRs are still Ready and functionally serving features

import (
	"fmt"
	"os"

	. "github.com/feast-dev/feast/infra/feast-operator/test/e2e_rhoai/utils"
	. "github.com/onsi/ginkgo/v2"
	. "github.com/onsi/gomega"
)

var _ = Describe("Feast Modular Architecture Upgrade Testing", Ordered, func() {
	const (
		namespace           = "test-ns-feast-upgrade"
		testDir             = "/test/e2e_rhoai"
		feastCRName         = "test-s3"
		feastDeploymentName = FeastPrefix + "test-s3"
	)

	testTier := os.Getenv("TEST_TIER")

	Context("Pre-Upgrade: snapshot baselines before ODH upgrade", func() {
		It("Should store feast-operator Deployment and FeatureStore CR baselines", func() {
			if testTier != "Pre-Upgrade" {
				Skip(fmt.Sprintf("Skipping Pre-Upgrade steps (TEST_TIER=%q)", testTier))
			}

			By("Storing feast-operator Deployment baseline for post-upgrade spec integrity check")
			StoreFeastOperatorDeploymentBaseline(namespace)

			By("Storing FeatureStore CR baseline for post-upgrade spec integrity check")
			StoreFeatureStoreBaseline(namespace, feastCRName)
		})
	})

	Context("Post-Upgrade: verify spec integrity and operand continuity after ODH upgrade", func() {
		It("Should verify feast-operator Deployment spec and FeatureStore CR spec are unchanged", func() {
			if testTier != "Post-Upgrade" {
				Skip(fmt.Sprintf("Skipping Post-Upgrade steps (TEST_TIER=%q)", testTier))
			}

			By("Verifying feast-operator Deployment spec was not mutated by the module controller")
			VerifyFeastOperatorDeploymentIntegrity(namespace)

			By("Verifying FeatureStore CR spec was not mutated during upgrade")
			VerifyFeatureStoreSpecIntegrity(namespace, feastCRName)

			By("Verifying feast operand pods had zero restarts — serving was uninterrupted")
			VerifyNoPodRestarts(namespace, feastDeploymentName)

			By("Verifying feast-operator Deployment is still available after upgrade")
			CheckDeployment(namespace, feastDeploymentName)

			By("Verifying FeatureStore CR is still in Ready state after upgrade")
			ValidateFeatureStoreCRStatus(namespace, feastCRName)

			By("Verifying feature_store.yaml still contains correct S3 registry configuration")
			ValidateFeatureStoreYamlS3(namespace, feastDeploymentName)

			By("Verifying pre-upgrade registry objects are intact in S3 after upgrade")
			ValidateRegistryIntact(namespace, feastDeploymentName, testDir)

			By("Verifying materialization intervals are preserved after upgrade")
			ValidateMaterializationIntervals(namespace, feastDeploymentName, testDir)

			By("Verifying online feature serving is still queryable after upgrade")
			VerifyOnlineFeatureServing(namespace, feastDeploymentName, testDir)
		})
	})
})
