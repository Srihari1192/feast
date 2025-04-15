package e2e_rhoai

import (
	"fmt"
	"os/exec"

	"github.com/feast-dev/feast/infra/feast-operator/test/utils"
	. "github.com/onsi/ginkgo/v2"
	. "github.com/onsi/gomega"
)

var _ = Describe("controller", Ordered, func() {
	featureStoreName := "simple-feast-setup"
	feastResourceName := utils.FeastPrefix + featureStoreName
	feastK8sResourceNames := []string{
		feastResourceName + "-online",
		feastResourceName + "-offline",
		feastResourceName + "-ui",
	}

	testDir := "/test/e2e_rhoai"
	crYaml := "test/testdata/feast_integration_test_crs/v1alpha1_default_featurestore.yaml"
	remoteRegistryCRYaml := "test/testdata/feast_integration_test_crs/v1alpha1_remote_registry_featurestore.yaml"
	namespace := "test-ns-feast"

	BeforeAll(func() {
		By(fmt.Sprintf("Creating test namespace: %s", namespace))
		utils.CreateNamespace(namespace, testDir)

	})

	AfterAll(func() {
		By(fmt.Sprintf("Deleting test namespace: %s", namespace))
		utils.DeleteNamespace(namespace, testDir)
	})

	Context("Feast Operator E2E Tests", func() {
		It("Should be able to deploy and TesDefaultFeastCR successfully", func() {
			By("Deploying the Simple Feast Custom Resource to Kubernetes")

			utils.CreateNamespace(namespace, testDir)

			cmd := exec.Command("kubectl", "apply", "-f", crYaml, "-n", namespace)
			_, err := utils.Run(cmd, testDir)
			ExpectWithOffset(1, err).NotTo(HaveOccurred())

			utils.ValidateTheFeatureStoreCustomResource(namespace, featureStoreName, feastResourceName, feastK8sResourceNames, utils.Timeout)

			By("Deleting the Feast deployment")
			cmd = exec.Command("kubectl", "delete", "-f", crYaml, "-n", namespace)
			_, err = utils.Run(cmd, testDir)
			ExpectWithOffset(1, err).NotTo(HaveOccurred())

			utils.DeleteNamespace(namespace, testDir)
		})

		It("Should be able to deploy and TestRemoteRegistryFeastCR successfully", func() {
			// utils.CreateNamespace(namespace, testDir)

			By("deploying the Simple Feast Custom Resource to Kubernetes")
			cmd := exec.Command("kubectl", "apply", "-f", crYaml, "-n", namespace)
			_, cmdOutputErr := utils.Run(cmd, testDir)
			ExpectWithOffset(1, cmdOutputErr).NotTo(HaveOccurred())

			utils.ValidateTheFeatureStoreCustomResource(namespace, featureStoreName, feastResourceName, feastK8sResourceNames, utils.Timeout)

			remoteRegistryNs := "test-remote-registry"

			By(fmt.Sprintf("Creating remote registry namespace: %s", remoteRegistryNs))
			utils.CreateNamespace(remoteRegistryNs, testDir)

			DeferCleanup(func() {
				By(fmt.Sprintf("Deleting remote registry namespace: %s", remoteRegistryNs))
				utils.DeleteNamespace(remoteRegistryNs, testDir)
			})

			By("deploying the Simple Feast remote registry Custom Resource on Kubernetes")
			cmd = exec.Command("kubectl", "apply", "-f", remoteRegistryCRYaml, "-n", remoteRegistryNs)
			_, cmdOutputErr = utils.Run(cmd, testDir)
			ExpectWithOffset(1, cmdOutputErr).NotTo(HaveOccurred())

			remoteFeatureStoreName := "simple-feast-remote-setup"
			remoteFeastResourceName := utils.FeastPrefix + remoteFeatureStoreName
			utils.FixRemoteFeastK8sResourceNames(feastK8sResourceNames, remoteFeastResourceName)
			utils.ValidateTheFeatureStoreCustomResource(remoteRegistryNs, remoteFeatureStoreName, remoteFeastResourceName, feastK8sResourceNames, utils.Timeout)

			By("deleting the feast remote registry deployment")
			cmd = exec.Command("kubectl", "delete", "-f", remoteRegistryCRYaml, "-n", remoteRegistryNs)
			_, cmdOutputErr = utils.Run(cmd, testDir)
			ExpectWithOffset(1, cmdOutputErr).NotTo(HaveOccurred())

			By("deleting the feast deployment")
			cmd = exec.Command("kubectl", "delete", "-f", crYaml, "-n", namespace)
			_, cmdOutputErr = utils.Run(cmd, testDir)
			ExpectWithOffset(1, cmdOutputErr).NotTo(HaveOccurred())

		})
	})
})
