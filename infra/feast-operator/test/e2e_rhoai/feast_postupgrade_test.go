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

import (
	"fmt"


	. "github.com/feast-dev/feast/infra/feast-operator/test/utils"
	. "github.com/onsi/ginkgo/v2"
	. "github.com/onsi/gomega"
)

var _ = Describe("Feast PostUpgrade scenario Testing", Ordered, func() {
	const (
		namespace       = "test-ns-feast"
		testDir         = "/test/e2e_rhoai"
		feastDeploymentName = FeastPrefix+"credit-scoring"
		feastCRName = "credit-scoring"
	)

	AfterAll(func() {
		By(fmt.Sprintf("Deleting test namespace: %s", namespace))
		Expect(DeleteNamespace(namespace, testDir)).To(Succeed())
		fmt.Printf("Namespace %s deleted successfully\n", namespace)
	})
	runPreUpgradeTest := func() {
		fmt.Println("Post upgrade test ran successfully")
		By("Verify Feature Store CR is in Ready state")
		ValidateFeatureStoreCRStatus(namespace, feastCRName)
		VerifyApplyFeatureStoreDefinitions(namespace, feastCRName, feastDeploymentName)
		VerifyFeastMethods(namespace, feastDeploymentName, testDir)
		TrainAndTestModel(namespace, feastCRName, feastDeploymentName, testDir)
	}
	Context("Feast post Upgrade Test", func() {
		It("Should create and run a postUpgrade test scenario successfully", runPreUpgradeTest)
	})
})