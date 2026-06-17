package utils

import (
	"bytes"
	"fmt"
	"os"
	"os/exec"
	"regexp"
	"strings"

	testutils "github.com/feast-dev/feast/infra/feast-operator/test/utils"
	. "github.com/onsi/ginkgo/v2"
	. "github.com/onsi/gomega"
)

const (
	FeastPrefix = "feast-"
)

// logCronJobCommands prints CronJob command list read from FeatureStore CR status.
func logCronJobCommands(commands string) {
	fmt.Printf("CronJob commands from CR status:\n  %s\n\n", strings.TrimSpace(commands))
}

func ListConfigMaps(namespace string) ([]string, error) {
	cmd := exec.Command("kubectl", "get", "cm", "-n", namespace, "-o", "jsonpath={range .items[*]}{.metadata.name}{\"\\n\"}{end}")
	var out bytes.Buffer
	var stderr bytes.Buffer
	cmd.Stdout = &out
	cmd.Stderr = &stderr

	if err := cmd.Run(); err != nil {
		return nil, fmt.Errorf("failed to list config maps in namespace %s. Error: %v. Stderr: %s",
			namespace, err, stderr.String())
	}

	configMaps := strings.Split(strings.TrimSpace(out.String()), "\n")
	// Filter out empty strings
	var result []string
	for _, cm := range configMaps {
		if cm != "" {
			result = append(result, cm)
		}
	}
	return result, nil
}

// VerifyConfigMapExistsInList checks if a ConfigMap exists in the list of ConfigMaps
func VerifyConfigMapExistsInList(namespace, configMapName string) (bool, error) {
	configMaps, err := ListConfigMaps(namespace)
	if err != nil {
		return false, err
	}

	for _, cm := range configMaps {
		if cm == configMapName {
			return true, nil
		}
	}

	return false, nil
}

// checkIfConfigMapExists validates if a config map exists using the kubectl CLI.
func checkIfConfigMapExists(namespace, configMapName string) error {
	cmd := exec.Command("kubectl", "get", "cm", configMapName, "-n", namespace)

	var out bytes.Buffer
	var stderr bytes.Buffer
	cmd.Stdout = &out
	cmd.Stderr = &stderr

	if err := cmd.Run(); err != nil {
		return fmt.Errorf("failed to find config map %s in namespace %s. Error: %v. Stderr: %s",
			configMapName, namespace, err, stderr.String())
	}

	// Check the output to confirm presence
	if !strings.Contains(out.String(), configMapName) {
		return fmt.Errorf("config map %s not found in namespace %s", configMapName, namespace)
	}

	return nil
}

// VerifyFeastConfigMapExists verifies that a ConfigMap exists and contains the specified key/file
func VerifyFeastConfigMapExists(namespace, configMapName, expectedKey string) error {
	// First verify the ConfigMap exists
	if err := checkIfConfigMapExists(namespace, configMapName); err != nil {
		return fmt.Errorf("config map %s does not exist: %w", configMapName, err)
	}

	// Get the ConfigMap data to verify the key exists
	cmd := exec.Command("kubectl", "get", "cm", configMapName, "-n", namespace, "-o", "jsonpath={.data."+expectedKey+"}")
	var out bytes.Buffer
	var stderr bytes.Buffer
	cmd.Stdout = &out
	cmd.Stderr = &stderr

	if err := cmd.Run(); err != nil {
		return fmt.Errorf("failed to get config map data for %s in namespace %s. Error: %v. Stderr: %s",
			configMapName, namespace, err, stderr.String())
	}

	configContent := out.String()
	if configContent == "" {
		return fmt.Errorf("config map %s does not contain key %s", configMapName, expectedKey)
	}

	return nil
}

// VerifyFeastConfigMapContent verifies that a ConfigMap contains the expected feast configuration content
// This assumes the ConfigMap and key already exist (use VerifyFeastConfigMapExists first)
func VerifyFeastConfigMapContent(namespace, configMapName, expectedKey string, expectedContent []string) error {
	// Get the ConfigMap data
	cmd := exec.Command("kubectl", "get", "cm", configMapName, "-n", namespace, "-o", "jsonpath={.data."+expectedKey+"}")
	var out bytes.Buffer
	var stderr bytes.Buffer
	cmd.Stdout = &out
	cmd.Stderr = &stderr

	if err := cmd.Run(); err != nil {
		return fmt.Errorf("failed to get config map data for %s in namespace %s. Error: %v. Stderr: %s",
			configMapName, namespace, err, stderr.String())
	}

	configContent := out.String()
	if configContent == "" {
		return fmt.Errorf("config map %s does not contain key %s", configMapName, expectedKey)
	}

	// Verify all expected content strings are present
	for _, expected := range expectedContent {
		if !strings.Contains(configContent, expected) {
			return fmt.Errorf("config map %s content does not contain expected string: %s. Content:\n%s",
				configMapName, expected, configContent)
		}
	}

	return nil
}

func ApplyFeastPermissions(fileName string, registryFilePath string, namespace string, podNamePrefix string) {
	By("Applying Feast permissions to the Feast registry pod")

	// 1. Get the pod by prefix
	By(fmt.Sprintf("Finding pod with prefix %q in namespace %q", podNamePrefix, namespace))
	pod, err := getPodByPrefix(namespace, podNamePrefix)
	ExpectWithOffset(1, err).NotTo(HaveOccurred())
	ExpectWithOffset(1, pod).NotTo(BeNil())

	podName := pod.Name
	fmt.Printf("Found pod: %s\n", podName)

	ExpectWithOffset(1, registryFilePath).To(And(
		Not(BeEmpty()),
		HavePrefix("/"),
		Not(ContainSubstring("\x00")),
	), "registryFilePath must be a non-empty absolute container path")

	cmd := exec.Command(
		"oc", "exec", "-i", podName,
		"-n", namespace,
		"-c", "registry",
		"--",
		"sh", "-c", `cat > "$1"`, "sh", registryFilePath,
	)

	file, err := os.Open(fileName)
	ExpectWithOffset(1, err).NotTo(HaveOccurred(), "Failed to open permissions file")
	defer func() {
		if cerr := file.Close(); cerr != nil {
			fmt.Printf("Warning: failed to close file: %v\n", cerr)
		}
	}()
	cmd.Stdin = file

	output, err := cmd.CombinedOutput()
	ExpectWithOffset(1, err).NotTo(HaveOccurred(),
		fmt.Sprintf("Failed to copy file to pod: %s", string(output)))

	fmt.Printf("Successfully copied file to pod: %s\n", podName)

	// Run `feast apply` inside the pod to apply updated permissions
	By("Running feast apply inside the Feast registry pod")
	cmd = exec.Command(
		"oc", "exec", podName,
		"-n", namespace,
		"-c", "registry",
		"--",
		"bash", "-c",
		"cd /feast-data/credit_scoring_local/feature_repo && feast apply",
	)
	_, err = testutils.Run(cmd, "/test/e2e_rhoai")
	ExpectWithOffset(1, err).NotTo(HaveOccurred())
	fmt.Println("Feast permissions apply executed successfully")

	By("Validating that Feast permission has been applied")

	cmd = exec.Command(
		"oc", "exec", podName,
		"-n", namespace,
		"-c", "registry",
		"--",
		"feast", "permissions", "list",
	)

	output, err = testutils.Run(cmd, "/test/e2e_rhoai")
	ExpectWithOffset(1, err).NotTo(HaveOccurred())

	// Change "feast-auth" if your permission name is different
	ExpectWithOffset(1, output).To(ContainSubstring("feast-auth"), "Expected permission 'feast-auth' to exist")

	fmt.Println("Verified: Feast permission 'feast-auth' exists")
}

// CreateNamespace - create the namespace for tests
func CreateNamespace(namespace string, testDir string) error {
	cmd := exec.Command("kubectl", "create", "ns", namespace)
	output, err := testutils.Run(cmd, testDir)
	if err != nil {
		return fmt.Errorf("failed to create namespace %s: %v\nOutput: %s", namespace, err, output)
	}
	return nil
}

// DeleteNamespace - Delete the namespace for tests
func DeleteNamespace(namespace string, testDir string) error {
	cmd := exec.Command("kubectl", "delete", "ns", namespace, "--timeout=180s")
	output, err := testutils.Run(cmd, testDir)
	if err != nil {
		return fmt.Errorf("failed to delete namespace %s: %v\nOutput: %s", namespace, err, output)
	}
	return nil
}

// applies the manifests for Redis and Postgres and checks whether the deployments become available
func ApplyFeastInfraManifestsAndVerify(namespace string, testDir string) {
	By("Applying postgres.yaml and redis.yaml manifests")
	cmd := exec.Command("kubectl", "apply", "-n", namespace, "-f", "test/testdata/feast_integration_test_crs/postgres.yaml", "-f", "test/testdata/feast_integration_test_crs/redis.yaml")
	_, cmdOutputerr := testutils.Run(cmd, testDir)
	ExpectWithOffset(1, cmdOutputerr).NotTo(HaveOccurred())
	CheckDeployment(namespace, "postgres")
	CheckDeployment(namespace, "redis")
}

// substituteFeastS3Credentials replaces ${AWS_ACCESS_KEY_ID}, ${AWS_S3_BUCKET}, etc. from process environment variables.
// Returns an error if any required variable is unset or whitespace-only so the test fails before kubectl apply.
func substituteFeastS3Credentials(content string) (string, error) {
	required := []struct {
		env, placeholder string
	}{
		{"AWS_ACCESS_KEY_ID", "${AWS_ACCESS_KEY_ID}"},
		{"AWS_SECRET_ACCESS_KEY", "${AWS_SECRET_ACCESS_KEY}"},
		{"AWS_DEFAULT_REGION", "${AWS_DEFAULT_REGION}"},
		{"AWS_S3_BUCKET", "${AWS_S3_BUCKET}"},
	}
	repl := make(map[string]string, len(required))
	var missing []string
	for _, r := range required {
		v := strings.TrimSpace(os.Getenv(r.env))
		if v == "" {
			missing = append(missing, r.env)
			continue
		}
		repl[r.placeholder] = v
	}
	if len(missing) > 0 {
		return "", fmt.Errorf(
			"feast S3 e2e requires non-empty environment variables: %s",
			strings.Join(missing, ", "),
		)
	}
	out := content
	for ph, v := range repl {
		out = strings.ReplaceAll(out, ph, v)
	}
	return out, nil
}

// ApplyFeastS3YamlAndVerify applies the S3-based FeatureStore manifest (no postgres/redis),
// waits for deployment FeastPrefix+feastCRName, validates CR Ready and feature_store.yaml for S3 driver_ranking.
// feastCRName must match FeatureStore metadata.name in the manifest (e.g. "test-s3").
func ApplyFeastS3YamlAndVerify(namespace string, testDir string, feastS3YamlPath string, feastCRName string) {
	By("Applying Feast S3 manifest (secrets + FeatureStore CR)")
	data, err := os.ReadFile(feastS3YamlPath)
	ExpectWithOffset(1, err).NotTo(HaveOccurred())

	rendered, err := substituteFeastS3Credentials(string(data))
	ExpectWithOffset(1, err).NotTo(HaveOccurred())
	tmp, err := os.CreateTemp("", "feast-s3-rendered-*.yaml")
	ExpectWithOffset(1, err).NotTo(HaveOccurred())
	tmpPath := tmp.Name()
	defer func() {
		if rmErr := os.Remove(tmpPath); rmErr != nil {
			fmt.Fprintf(os.Stderr, "warning: could not remove temp file %s: %v\n", tmpPath, rmErr)
		}
	}()
	_, err = tmp.WriteString(rendered)
	ExpectWithOffset(1, err).NotTo(HaveOccurred())
	ExpectWithOffset(1, tmp.Close()).To(Succeed())

	cmd := exec.Command("kubectl", "apply", "-n", namespace, "-f", tmpPath)
	_, err = testutils.Run(cmd, testDir)
	ExpectWithOffset(1, err).NotTo(HaveOccurred())
	fmt.Printf("Applied rendered Feast S3 manifest to namespace %q\n", namespace)

	feastDeploymentName := FeastPrefix + feastCRName
	CheckDeployment(namespace, feastDeploymentName)
	ValidateFeatureStoreCRStatus(namespace, feastCRName)

	By("Verifying client feature_store.yaml for S3-backed registry and driver_ranking project")
	validateFeatureStoreYamlS3(namespace, feastDeploymentName)
}

// CheckDeployment verifies the specified deployment exists and is in the "Available" state.
func CheckDeployment(namespace, name string) {
	By(fmt.Sprintf("Waiting for %s deployment to become available", name))
	err := testutils.CheckIfDeploymentExistsAndAvailable(namespace, name, 2*testutils.Timeout)
	Expect(err).ToNot(HaveOccurred(), fmt.Sprintf(
		"Deployment %s is not available but expected to be.\nError: %v", name, err,
	))
	fmt.Printf("Deployment %s is available\n", name)
}

// validate that the status of the FeatureStore CR is "Ready".
func ValidateFeatureStoreCRStatus(namespace, crName string) {
	lastObservation := "no observation captured yet"
	Eventually(func() string {
		cmd := exec.Command("kubectl", "get", "feast", crName, "-n", namespace, "-o", "jsonpath={.status.phase}")
		output, err := cmd.CombinedOutput()
		if err != nil {
			lastObservation = fmt.Sprintf("kubectl get failed: %v; output: %s", err, strings.TrimSpace(string(output)))
			return ""
		}
		phase := strings.TrimSpace(string(output))
		lastObservation = fmt.Sprintf("status.phase=%q", phase)
		return phase
	}, "5m", "5s").Should(
		Equal("Ready"),
		fmt.Sprintf(
			"Feature Store CR %s/%s did not reach 'Ready' state in time; last observation: %s",
			namespace, crName, lastObservation,
		),
	)

	fmt.Printf("Feature Store CR is Ready: %s/%s\n", namespace, crName)
}

// verifyApplyFeatureStoreCronJob checks CronJob commands on the FeatureStore CR and runs a one-off apply job with expected log substrings.
func verifyApplyFeatureStoreCronJob(namespace, feastCRName, feastDeploymentName, testDir string, expectedJobLogs []string) {
	By("Verify CronJob commands in FeatureStore CR")
	cmd := exec.Command("kubectl", "get", "-n", namespace, "feast/"+feastCRName, "-o", "jsonpath={.status.applied.cronJob.containerConfigs.commands}")
	output, err := cmd.CombinedOutput()
	Expect(err).NotTo(HaveOccurred(), fmt.Sprintf("Failed to fetch CronJob commands:\n%s", output))
	commands := string(output)
	logCronJobCommands(commands)
	Expect(commands).To(ContainSubstring(`feast apply`))
	Expect(commands).To(ContainSubstring(`feast materialize-incremental $(date -u +'%Y-%m-%dT%H:%M:%S')`))

	CreateAndVerifyJobFromCron(namespace, feastDeploymentName, "feast-test-apply", testDir, expectedJobLogs)
}

// validates the `feast apply` and `feast materialize-incremental commands were configured in the FeatureStore CR's CronJob config.
func VerifyApplyFeatureStoreDefinitions(namespace string, feastCRName string, feastDeploymentName string) {
	verifyApplyFeatureStoreCronJob(namespace, feastCRName, feastDeploymentName, "", []string{
		"No project found in the repository",
		"Applying changes for project credit_scoring_local",
		"Deploying infrastructure for credit_history",
		"Deploying infrastructure for zipcode_features",
		"Materializing 2 feature views to",
		"into the redis online store",
		"credit_history from",
		"zipcode_features from",
	})
}

// VerifyApplyFeatureStoreDefinitionsS3 validates the apply/materialize CronJob for the S3 + driver_ranking FeatureStore.
func VerifyApplyFeatureStoreDefinitionsS3(namespace string, feastCRName string, feastDeploymentName string) {
	verifyApplyFeatureStoreCronJob(namespace, feastCRName, feastDeploymentName, "", []string{
		"Applying changes for project driver_ranking",
		"Materializing",
	})
}

// Create a Job and verifies its logs contain expected substrings
func CreateAndVerifyJobFromCron(namespace, cronName, jobName, testDir string, expectedLogSubstrings []string) {
	By(fmt.Sprintf("Creating Job %s from CronJob %s", jobName, cronName))
	cmd := exec.Command("kubectl", "create", "job", "--from=cronjob/"+cronName, jobName, "-n", namespace)
	_, err := testutils.Run(cmd, testDir)
	ExpectWithOffset(1, err).NotTo(HaveOccurred())
	fmt.Printf("Created one-off job %q from CronJob %q\n", jobName, cronName)

	By("Waiting for Job completion")
	cmd = exec.Command("kubectl", "wait", "--for=condition=complete", "--timeout=5m", "job/"+jobName, "-n", namespace)
	_, err = testutils.Run(cmd, testDir)
	ExpectWithOffset(1, err).NotTo(HaveOccurred())
	fmt.Printf("Job %q completed successfully\n", jobName)

	By("Checking logs of completed job")
	cmd = exec.Command("kubectl", "logs", "job/"+jobName, "-n", namespace, "--all-containers=true")
	output, err := testutils.Run(cmd, testDir)
	ExpectWithOffset(1, err).NotTo(HaveOccurred())

	outputStr := string(output)
	ansi := regexp.MustCompile(`\x1b\[[0-9;]*m`)
	outputStr = ansi.ReplaceAllString(outputStr, "")
	fmt.Printf("----- begin logs: job/%s namespace/%s -----\n%s\n----- end logs: job/%s -----\n",
		jobName, namespace, strings.TrimRight(outputStr, "\n"), jobName)

	for _, expected := range expectedLogSubstrings {
		ExpectWithOffset(1, outputStr).To(ContainSubstring(expected),
			fmt.Sprintf("job %q logs should contain substring %q", jobName, expected))
	}
	fmt.Printf("Job %q: all %d expected log substring(s) found: %v\n\n",
		jobName, len(expectedLogSubstrings), expectedLogSubstrings)
}

// validate the feature store yaml
func validateFeatureStoreYaml(namespace, deployment string) {
	cmd := exec.Command("kubectl", "exec", "deploy/"+deployment, "-n", namespace, "-c", "online", "--", "cat", "feature_store.yaml")
	output, err := cmd.CombinedOutput()
	Expect(err).NotTo(HaveOccurred(), "Failed to read feature_store.yaml")

	content := string(output)
	Expect(content).To(ContainSubstring("offline_store:\n    type: duckdb"))
	Expect(content).To(ContainSubstring("online_store:\n    type: redis"))
	Expect(content).To(ContainSubstring("registry_type: sql"))
}

func validateFeatureStoreYamlS3(namespace, deployment string) {
	cmd := exec.Command("kubectl", "exec", "deploy/"+deployment, "-n", namespace, "-c", "online", "--", "cat", "feature_store.yaml")
	output, err := cmd.CombinedOutput()
	Expect(err).NotTo(HaveOccurred(), "Failed to read feature_store.yaml")

	content := string(output)
	Expect(content).To(ContainSubstring("project: driver_ranking"))
	Expect(content).To(ContainSubstring("s3://"))
	fmt.Printf("feature_store.yaml in online pod: contains project driver_ranking and s3:// registry path\n")
}

// apply and verifies the Feast deployment becomes available, the CR status is "Ready
func ApplyFeastYamlAndVerify(namespace string, testDir string, feastDeploymentName string, feastCRName string, feastYAMLFilePath string) {
	By("Applying Feast yaml for secrets and Feature store CR")
	cmd := exec.Command("kubectl", "apply", "-n", namespace,
		"-f", feastYAMLFilePath)
	_, err := testutils.Run(cmd, testDir)
	ExpectWithOffset(1, err).NotTo(HaveOccurred())
	CheckDeployment(namespace, feastDeploymentName)

	By("Verify Feature Store CR is in Ready state")
	ValidateFeatureStoreCRStatus(namespace, feastCRName)

	By("Verifying that the Postgres DB contains the expected Feast tables")
	cmd = exec.Command("kubectl", "exec", "deploy/postgres", "-n", namespace, "--", "psql", "-h", "localhost", "-U", "feast", "feast", "-c", `\dt`)
	output, err := cmd.CombinedOutput()
	Expect(err).NotTo(HaveOccurred(), fmt.Sprintf("Failed to get tables from Postgres. Output:\n%s", output))
	outputStr := string(output)
	fmt.Println("Postgres Tables:\n", outputStr)
	// List of expected tables
	expectedTables := []string{
		"data_sources", "entities", "feast_metadata", "feature_services", "feature_views",
		"managed_infra", "on_demand_feature_views", "permissions", "projects",
		"saved_datasets", "stream_feature_views", "validation_references",
	}
	for _, table := range expectedTables {
		Expect(outputStr).To(ContainSubstring(table), fmt.Sprintf("Expected table %q not found in output:\n%s", table, outputStr))
	}

	By("Verifying that the Feast repo was successfully cloned by the init container")
	cmd = exec.Command("kubectl", "logs", "-f", "-n", namespace, "deploy/"+feastDeploymentName, "-c", "feast-init")
	output, err = cmd.CombinedOutput()
	Expect(err).NotTo(HaveOccurred(), fmt.Sprintf("Failed to get logs from init container. Output:\n%s", output))
	outputStr = string(output)
	fmt.Println("Init Container Logs:\n", outputStr)
	// Assert that the logs contain success indicators
	Expect(outputStr).To(ContainSubstring("Feast repo creation complete"), "Expected Feast repo creation message not found")

	By("Verifying client feature_store.yaml for expected store types")
	validateFeatureStoreYaml(namespace, feastDeploymentName)
}

// checks for the presence of expected entities, features, feature views, data sources, etc.
func VerifyFeastMethods(namespace string, feastDeploymentName string, testDir string) {
	type feastCheck struct {
		command   []string
		expected  []string
		logPrefix string
	}
	checks := []feastCheck{
		{
			command:   []string{"feast", "projects", "list"},
			expected:  []string{"credit_scoring_local"},
			logPrefix: "Projects List",
		},
		{
			command:   []string{"feast", "feature-views", "list"},
			expected:  []string{"credit_history", "zipcode_features", "total_debt_calc"},
			logPrefix: "Feature Views List",
		},
		{
			command:   []string{"feast", "entities", "list"},
			expected:  []string{"zipcode", "dob_ssn"},
			logPrefix: "Entities List",
		},
		{
			command:   []string{"feast", "data-sources", "list"},
			expected:  []string{"Zipcode source", "Credit history", "application_data"},
			logPrefix: "Data Sources List",
		},
		{
			command: []string{"feast", "features", "list"},
			expected: []string{
				"credit_card_due", "mortgage_due", "student_loan_due", "vehicle_loan_due",
				"hard_pulls", "missed_payments_2y", "missed_payments_1y", "missed_payments_6m",
				"bankruptcies", "city", "state", "location_type", "tax_returns_filed",
				"population", "total_wages", "total_debt_due",
			},
			logPrefix: "Features List",
		},
	}

	for _, check := range checks {
		cmd := exec.Command("kubectl", "exec", "deploy/"+feastDeploymentName, "-n", namespace, "-c", "online", "--")
		cmd.Args = append(cmd.Args, check.command...)
		output, err := testutils.Run(cmd, testDir)
		ExpectWithOffset(1, err).NotTo(HaveOccurred())

		fmt.Printf("%s:\n%s\n", check.logPrefix, string(output))
		VerifyOutputContains(output, check.expected)
	}
}

// ReplaceNamespaceInYaml reads a YAML file, replaces all existingNamespace with the actual namespace
func ReplaceNamespaceInYamlFilesInPlace(filePaths []string, existingNamespace string, actualNamespace string) error {
	for _, filePath := range filePaths {
		data, err := os.ReadFile(filePath)
		if err != nil {
			return fmt.Errorf("failed to read YAML file %s: %w", filePath, err)
		}
		updated := strings.ReplaceAll(string(data), existingNamespace, actualNamespace)

		err = os.WriteFile(filePath, []byte(updated), 0644)
		if err != nil {
			return fmt.Errorf("failed to write updated YAML file %s: %w", filePath, err)
		}
	}
	return nil
}

// asserts that all expected substrings are present in the given output.
func VerifyOutputContains(output []byte, expectedSubstrings []string) {
	outputStr := string(output)
	for _, expected := range expectedSubstrings {
		Expect(outputStr).To(ContainSubstring(expected), fmt.Sprintf("Expected output to contain: %s", expected))
	}
}

func ValidateFeatureStoreYamlS3(namespace, deployment string) {
	cmd := exec.Command("kubectl", "exec", "deploy/"+deployment, "-n", namespace, "-c", "online", "--", "cat", "feature_store.yaml")
	output, err := cmd.CombinedOutput()
	Expect(err).NotTo(HaveOccurred(), "Failed to read feature_store.yaml")

	content := string(output)
	Expect(content).To(ContainSubstring("project: driver_ranking"))
	Expect(content).To(ContainSubstring("s3://"))
	fmt.Printf("feature_store.yaml in online pod: contains project driver_ranking and s3:// registry path\n")
}

// ValidateMaterializationIntervals verifies that two properties survived the operator upgrade:
//
//  1. The K8s CronJob that drives feast materialize-incremental still has a non-empty
//     schedule — a schedule wipe would silently stop all future materialization.
//
//  2. The per-feature-view materialization bookmark (materializationIntervals in the S3 registry)
//     is intact. feast materialize-incremental uses the most recent interval endTime as its
//     start window; if the bookmark is wiped, the next run re-materializes from epoch (full scan).
//     If the bookmark was never written pre-upgrade (CronJob had not yet fired), the
//     materializationIntervals list is empty and this check is skipped with a warning.
func ValidateMaterializationIntervals(namespace string, feastDeploymentName string, testDir string) {
	By("Asserting CronJob schedule was not wiped by the upgrade")
	cmd := exec.Command("kubectl", "get", "cronjob", feastDeploymentName,
		"-n", namespace, "-o", "jsonpath={.spec.schedule}")
	output, err := testutils.Run(cmd, testDir)
	ExpectWithOffset(1, err).NotTo(HaveOccurred(),
		fmt.Sprintf("CronJob %s not found in namespace %s — upgrade may have deleted it", feastDeploymentName, namespace))
	schedule := strings.TrimSpace(string(output))
	ExpectWithOffset(1, schedule).NotTo(BeEmpty(),
		"CronJob schedule is empty — upgrade reset the materialization schedule")
	fmt.Printf("CronJob %s schedule: %q (preserved)\n", feastDeploymentName, schedule)

	for _, fv := range []string{"driver_hourly_stats", "driver_hourly_stats_fresh"} {
		By(fmt.Sprintf("Asserting materialization bookmark for feature view %s", fv))
		cmd = exec.Command(
			"kubectl", "exec", "deploy/"+feastDeploymentName,
			"-n", namespace, "-c", "online", "--",
			"feast", "feature-views", "describe", fv,
		)
		output, err = testutils.Run(cmd, testDir)
		ExpectWithOffset(1, err).NotTo(HaveOccurred(),
			fmt.Sprintf("feast feature-views describe %s failed — S3 registry may be inaccessible", fv))

		described := string(output)
		fmt.Printf("feast feature-views describe %s:\n%s\n", fv, described)

		ExpectWithOffset(1, described).To(ContainSubstring("materializationIntervals"),
			fmt.Sprintf("feature view %s: materializationIntervals field missing — "+
				"registry entry may have been reset by the upgrade", fv))

		if strings.Contains(described, "startTime") {
			ExpectWithOffset(1, described).NotTo(ContainSubstring("1970-01-01"),
				fmt.Sprintf("feature view %s: materializationIntervals contain epoch timestamp (1970-01-01) — "+
					"bookmark was reset by the upgrade; next incremental job will re-materialize from epoch", fv))
			fmt.Printf("Feature view %s: materialization bookmark intact (non-epoch startTime present)\n", fv)
		} else {
			fmt.Printf("NOTICE: feature view %s has no materializationIntervals — "+
				"CronJob may not have fired before the upgrade; bookmark check skipped\n", fv)
		}
	}
}

func ValidateRegistryIntact(namespace string, feastDeploymentName string, testDir string) {
	type feastCheck struct {
		command   []string
		expected  []string
		logPrefix string
	}

	checks := []feastCheck{
		{
			command:   []string{"feast", "projects", "list"},
			expected:  []string{"driver_ranking"},
			logPrefix: "Projects List",
		},
		{
			command:   []string{"feast", "feature-views", "list"},
			expected:  []string{"driver_hourly_stats", "driver_hourly_stats_fresh", "transformed_conv_rate", "transformed_conv_rate_fresh"},
			logPrefix: "Feature Views List",
		},
		{
			command:   []string{"feast", "entities", "list"},
			expected:  []string{"driver"},
			logPrefix: "Entities List",
		},
		{
			command:   []string{"feast", "data-sources", "list"},
			expected:  []string{"driver_hourly_stats_source", "vals_to_add", "driver_stats_push_source"},
			logPrefix: "Data Sources List",
		},
		{
			command: []string{"feast", "features", "list"},
			expected: []string{
				"conv_rate", "acc_rate", "avg_daily_trips",
				"driver_metadata", "driver_config", "driver_profile",
				"conv_rate_plus_val1", "conv_rate_plus_val2",
			},
			logPrefix: "Features List",
		},
		{
			command:   []string{"feast", "feature-services", "list"},
			expected:  []string{"driver_activity_v1", "driver_activity_v2"},
			logPrefix: "Feature Services List",
		},
	}

	for _, check := range checks {
		cmd := exec.Command("kubectl", "exec", "deploy/"+feastDeploymentName, "-n", namespace, "-c", "online", "--")
		cmd.Args = append(cmd.Args, check.command...)
		output, err := testutils.Run(cmd, testDir)
		ExpectWithOffset(1, err).NotTo(HaveOccurred(),
			fmt.Sprintf("feast %s list failed — registry may be inaccessible or objects wiped by upgrade", check.logPrefix))

		fmt.Printf("%s:\n%s\n", check.logPrefix, string(output))
		for _, expected := range check.expected {
			ExpectWithOffset(1, string(output)).To(ContainSubstring(expected),
				fmt.Sprintf("registry intact check: %s list missing %q — was the S3 registry wiped by the upgrade?", check.logPrefix, expected))
		}
	}
	fmt.Printf("Registry intact: all pre-upgrade objects present in S3 registry\n")
}

// VerifyFeastMethodsForDriverRanking confirms the driver_ranking project is listed post-apply.
// Unlike ValidateRegistryIntact it runs after feast apply and serves as a write-path smoke test.
func VerifyFeastMethodsForDriverRanking(namespace string, feastDeploymentName string, testDir string) {
	cmd := exec.Command("kubectl", "exec", "deploy/"+feastDeploymentName, "-n", namespace, "-c", "online", "--",
		"feast", "projects", "list")
	output, err := testutils.Run(cmd, testDir)
	ExpectWithOffset(1, err).NotTo(HaveOccurred())

	fmt.Printf("Command: feast projects list\nOutput:\n%s\n", string(output))
	VerifyOutputContains(output, []string{"driver_ranking"})
	fmt.Printf("Assertion OK: output contains expected substring driver_ranking\n")
}

func VerifyOnlineFeatureServing(namespace string, feastDeploymentName string, testDir string) {
	var output []byte
	var err error

	// Retry up to 3 times — the online store may take a moment to become ready
	// after the deployment rolls out during an upgrade.
	for attempt := 1; attempt <= 3; attempt++ {
		cmd := exec.Command(
			"kubectl", "exec", "deploy/"+feastDeploymentName,
			"-n", namespace, "-c", "online", "--",
			"feast", "get-online-features",
			"-e", "driver_id=1001",
			"-e", "driver_id=1002",
			"-f", "driver_hourly_stats:conv_rate",
			"-f", "driver_hourly_stats:acc_rate",
			"-f", "driver_hourly_stats:avg_daily_trips",
		)
		output, err = testutils.Run(cmd, testDir)
		if err == nil {
			break
		}
		if attempt < 3 {
			fmt.Printf("feast get-online-features attempt %d/3 failed: %v — retrying\n", attempt, err)
			cmd2 := exec.Command("sleep", "5")
			_, _ = testutils.Run(cmd2, testDir)
		}
	}
	ExpectWithOffset(1, err).NotTo(HaveOccurred(),
		fmt.Sprintf("feast get-online-features failed after 3 attempts — "+
			"online store may be inaccessible post-upgrade:\n%s", string(output)))

	response := string(output)
	fmt.Printf("feast get-online-features response:\n%s\n", response)

	for _, feature := range []string{"conv_rate", "acc_rate", "avg_daily_trips", "driver_id"} {
		ExpectWithOffset(1, response).To(ContainSubstring(feature),
			fmt.Sprintf("%q missing from get-online-features output — "+
				"online store schema may have changed or materialization did not write data", feature))
	}

	fmt.Printf("VerifyOnlineFeatureServing: online feature serving verified via feast get-online-features\n")
}

// ---------------------------------------------------------------------------
// Modular upgrade: spec integrity helpers
//
// These functions implement the same two-phase snapshot/compare pattern used
// by the Trainer upgrade tests (opendatahub-io/distributed-workloads#920):
//
//   Pre-upgrade:  store generation + spec JSON + ODH version in a ConfigMap
//   Post-upgrade: compare current generation; fail if changed and not allowlisted
// ---------------------------------------------------------------------------

const (
	upgradeBaselineConfigMap     = "feast-upgrade-baseline"
	operatorBaselineConfigMap    = "feast-operator-upgrade-baseline"
	baselineGenerationKey        = "featurestore-generation"
	baselineSpecKey              = "featurestore-spec"
	baselineODHVersionKey        = "odh-version"
	operatorDeploymentGenKey     = "operator-deployment-generation"
	operatorDeploymentSpecKey    = "operator-deployment-spec"
	feastOperatorDeploymentName  = "feast-operator-controller-manager"
	feastOperatorNamespace       = "feast-operator-system"
)

// StoreFeatureStoreBaseline snapshots the FeatureStore CR generation and spec into a
// ConfigMap so the post-upgrade test can detect mutations. The ODH version is read from
// the DSCI status and stored alongside for allowlist version-pair checks.
func StoreFeatureStoreBaseline(namespace, crName string) {
	By(fmt.Sprintf("Snapshotting FeatureStore %s/%s baseline for post-upgrade integrity check", namespace, crName))

	genOut, err := exec.Command("kubectl", "get", "feast", crName, "-n", namespace,
		"-o", "jsonpath={.metadata.generation}").CombinedOutput()
	ExpectWithOffset(1, err).NotTo(HaveOccurred(), "Failed to read FeatureStore generation")

	specOut, err := exec.Command("kubectl", "get", "feast", crName, "-n", namespace,
		"-o", "jsonpath={.spec}").CombinedOutput()
	ExpectWithOffset(1, err).NotTo(HaveOccurred(), "Failed to read FeatureStore spec")

	storeBaseline(namespace, upgradeBaselineConfigMap, map[string]string{
		baselineGenerationKey: strings.TrimSpace(string(genOut)),
		baselineSpecKey:       strings.TrimSpace(string(specOut)),
		baselineODHVersionKey: getODHVersionFromDSCI(),
	})

	fmt.Printf("Stored FeatureStore baseline: generation=%s\n", strings.TrimSpace(string(genOut)))
}

// VerifyFeatureStoreSpecIntegrity loads the pre-upgrade baseline ConfigMap and compares
// the FeatureStore CR's current generation against the snapshot. A generation change means
// the operator mutated the spec during upgrade — only acceptable for known upgrade paths
// listed in isSpecMutationExpected.
func VerifyFeatureStoreSpecIntegrity(namespace, crName string) {
	By(fmt.Sprintf("Verifying FeatureStore %s/%s spec was not mutated during upgrade", namespace, crName))

	cmData := loadBaseline(namespace, upgradeBaselineConfigMap)

	preGen := extractJSONField(cmData, baselineGenerationKey)
	preSpec := extractJSONField(cmData, baselineSpecKey)
	preVersion := extractJSONField(cmData, baselineODHVersionKey)

	genOut, err := exec.Command("kubectl", "get", "feast", crName, "-n", namespace,
		"-o", "jsonpath={.metadata.generation}").CombinedOutput()
	ExpectWithOffset(1, err).NotTo(HaveOccurred(), "Failed to read post-upgrade FeatureStore generation")
	postGen := strings.TrimSpace(string(genOut))

	postVersion := getODHVersionFromDSCI()

	if postGen != preGen {
		postSpec, _ := exec.Command("kubectl", "get", "feast", crName, "-n", namespace,
			"-o", "jsonpath={.spec}").CombinedOutput()
		fmt.Printf("FeatureStore generation changed: %s → %s\n", preGen, postGen)
		fmt.Printf("Pre-upgrade spec:  %s\n", preSpec)
		fmt.Printf("Post-upgrade spec: %s\n", strings.TrimSpace(string(postSpec)))

		ExpectWithOffset(1, preVersion).NotTo(BeEmpty(), "Pre-upgrade ODH version missing from baseline ConfigMap")
		ExpectWithOffset(1, postVersion).NotTo(BeEmpty(), "Post-upgrade ODH version not available from DSCI")
		ExpectWithOffset(1, isSpecMutationExpected(preVersion, postVersion)).To(BeTrue(),
			"Unexpected FeatureStore spec mutation for upgrade %s → %s", preVersion, postVersion)
		fmt.Printf("FeatureStore spec mutation is allowlisted for upgrade %s → %s\n", preVersion, postVersion)
	} else {
		fmt.Printf("FeatureStore generation unchanged after upgrade: %s (spec intact)\n", postGen)
	}
}

// StoreFeastOperatorDeploymentBaseline snapshots the feast-operator controller-manager
// Deployment generation and spec so the post-upgrade test can verify the module controller
// applied an identical spec (no unexpected pod restart due to spec drift).
func StoreFeastOperatorDeploymentBaseline(namespace string) {
	By(fmt.Sprintf("Snapshotting feast-operator Deployment %s/%s baseline", feastOperatorNamespace, feastOperatorDeploymentName))

	genOut, err := exec.Command("kubectl", "get", "deployment", feastOperatorDeploymentName,
		"-n", feastOperatorNamespace,
		"-o", "jsonpath={.metadata.generation}").CombinedOutput()
	ExpectWithOffset(1, err).NotTo(HaveOccurred(), "Failed to read feast-operator Deployment generation")

	specOut, err := exec.Command("kubectl", "get", "deployment", feastOperatorDeploymentName,
		"-n", feastOperatorNamespace,
		"-o", "jsonpath={.spec}").CombinedOutput()
	ExpectWithOffset(1, err).NotTo(HaveOccurred(), "Failed to read feast-operator Deployment spec")

	storeBaseline(namespace, operatorBaselineConfigMap, map[string]string{
		operatorDeploymentGenKey:  strings.TrimSpace(string(genOut)),
		operatorDeploymentSpecKey: strings.TrimSpace(string(specOut)),
		baselineODHVersionKey:     getODHVersionFromDSCI(),
	})

	fmt.Printf("Stored feast-operator Deployment baseline: generation=%s\n", strings.TrimSpace(string(genOut)))
}

// VerifyFeastOperatorDeploymentIntegrity loads the pre-upgrade Deployment baseline and
// compares the current generation. A generation change means the module controller changed
// the Deployment spec — this causes a pod restart and a gap in FeatureStore reconciliation.
// The check is version-aware: if the upgrade path is allowlisted the assertion is skipped.
func VerifyFeastOperatorDeploymentIntegrity(namespace string) {
	By(fmt.Sprintf("Verifying feast-operator Deployment %s/%s spec was not changed by module controller",
		feastOperatorNamespace, feastOperatorDeploymentName))

	cmData := loadBaseline(namespace, operatorBaselineConfigMap)

	preGen := extractJSONField(cmData, operatorDeploymentGenKey)
	preSpec := extractJSONField(cmData, operatorDeploymentSpecKey)
	preVersion := extractJSONField(cmData, baselineODHVersionKey)

	genOut, err := exec.Command("kubectl", "get", "deployment", feastOperatorDeploymentName,
		"-n", feastOperatorNamespace,
		"-o", "jsonpath={.metadata.generation}").CombinedOutput()
	ExpectWithOffset(1, err).NotTo(HaveOccurred(), "Failed to read post-upgrade feast-operator Deployment generation")
	postGen := strings.TrimSpace(string(genOut))

	postVersion := getODHVersionFromDSCI()

	if postGen != preGen {
		postSpec, _ := exec.Command("kubectl", "get", "deployment", feastOperatorDeploymentName,
			"-n", feastOperatorNamespace,
			"-o", "jsonpath={.spec}").CombinedOutput()
		fmt.Printf("feast-operator Deployment generation changed: %s → %s\n", preGen, postGen)
		fmt.Printf("Pre-upgrade spec:  %s\n", preSpec)
		fmt.Printf("Post-upgrade spec: %s\n", strings.TrimSpace(string(postSpec)))

		ExpectWithOffset(1, preVersion).NotTo(BeEmpty(), "Pre-upgrade ODH version missing from baseline ConfigMap")
		ExpectWithOffset(1, postVersion).NotTo(BeEmpty(), "Post-upgrade ODH version not available from DSCI")
		ExpectWithOffset(1, isSpecMutationExpected(preVersion, postVersion)).To(BeTrue(),
			"Unexpected feast-operator Deployment spec change for upgrade %s → %s — "+
				"module controller Helm chart differs from in-tree reconciler; this will cause a pod restart",
			preVersion, postVersion)
		fmt.Printf("feast-operator Deployment change is allowlisted for upgrade %s → %s\n", preVersion, postVersion)
	} else {
		fmt.Printf("feast-operator Deployment generation unchanged after upgrade: %s (no pod restart)\n", postGen)
	}
}

// VerifyNoPodRestarts checks that all pods owned by the given Deployment have zero
// container restarts. A restart during upgrade indicates the operand was interrupted.
func VerifyNoPodRestarts(namespace, deploymentName string) {
	By(fmt.Sprintf("Verifying zero pod restarts for deployment %s/%s", namespace, deploymentName))

	out, err := exec.Command("kubectl", "get", "pods", "-n", namespace,
		"-l", fmt.Sprintf("app=%s", deploymentName),
		"-o", "jsonpath={range .items[*]}{.metadata.name}:{range .status.containerStatuses[*]}{.restartCount},{end}|{end}",
	).CombinedOutput()
	ExpectWithOffset(1, err).NotTo(HaveOccurred(), "Failed to list pods for restart check")

	podData := strings.TrimSpace(string(out))
	if podData == "" {
		// Fallback: list all pods in namespace and filter by name prefix
		out, err = exec.Command("kubectl", "get", "pods", "-n", namespace,
			"-o", "jsonpath={range .items[*]}{.metadata.name}:{range .status.containerStatuses[*]}{.restartCount},{end}|{end}",
		).CombinedOutput()
		ExpectWithOffset(1, err).NotTo(HaveOccurred(), "Failed to list pods for restart check (fallback)")
		podData = strings.TrimSpace(string(out))
	}

	fmt.Printf("Pod restart data for %s/%s: %s\n", namespace, deploymentName, podData)

	if podData == "" {
		fmt.Printf("NOTICE: no pods found for deployment %s — skipping restart check\n", deploymentName)
		return
	}

	for _, entry := range strings.Split(podData, "|") {
		entry = strings.TrimSpace(entry)
		if entry == "" {
			continue
		}
		parts := strings.SplitN(entry, ":", 2)
		if len(parts) != 2 {
			continue
		}
		podName, counts := parts[0], parts[1]
		for _, c := range strings.Split(strings.TrimSuffix(counts, ","), ",") {
			c = strings.TrimSpace(c)
			if c == "" {
				continue
			}
			ExpectWithOffset(1, c).To(Equal("0"),
				fmt.Sprintf("Pod %s has restart count %s — operand was restarted during upgrade", podName, c))
		}
		fmt.Printf("Pod %s: zero restarts confirmed\n", podName)
	}
}

// storeBaseline writes key/value pairs into a ConfigMap in the given namespace.
// Deletes any existing ConfigMap with the same name first.
func storeBaseline(namespace, configMapName string, data map[string]string) {
	pairs := ""
	for k, v := range data {
		pairs += fmt.Sprintf("  %s: %q\n", k, v)
	}
	manifest := fmt.Sprintf("apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: %s\n  namespace: %s\ndata:\n%s",
		configMapName, namespace, pairs)

	_ = exec.Command("kubectl", "delete", "cm", configMapName, "-n", namespace, "--ignore-not-found").Run()

	applyCmd := exec.Command("kubectl", "apply", "-f", "-", "-n", namespace)
	applyCmd.Stdin = strings.NewReader(manifest)
	out, err := applyCmd.CombinedOutput()
	ExpectWithOffset(1, err).NotTo(HaveOccurred(),
		fmt.Sprintf("Failed to create baseline ConfigMap %s/%s: %s", namespace, configMapName, out))
	fmt.Printf("Stored upgrade baseline in ConfigMap %s/%s\n", namespace, configMapName)
}

// loadBaseline reads the data field of a ConfigMap and returns it as a raw JSON string
// that extractJSONField can parse. Fails the test if the ConfigMap is missing.
func loadBaseline(namespace, configMapName string) string {
	out, err := exec.Command("kubectl", "get", "cm", configMapName, "-n", namespace,
		"-o", "jsonpath={.data}").CombinedOutput()
	ExpectWithOffset(1, err).NotTo(HaveOccurred(),
		fmt.Sprintf("Baseline ConfigMap %s/%s not found — pre-upgrade snapshot missing", namespace, configMapName))
	return string(out)
}

// getODHVersionFromDSCI reads the ODH/RHOAI version from DSCI status.release.version.
// Returns empty string on error so callers can decide whether to fail or skip.
func getODHVersionFromDSCI() string {
	out, err := exec.Command("kubectl", "get", "dsci", "-A",
		"-o", "jsonpath={.items[0].status.release.version}").CombinedOutput()
	if err != nil {
		fmt.Printf("Warning: could not read ODH version from DSCI: %v\n", err)
		return ""
	}
	return strings.TrimSpace(string(out))
}

// extractJSONField extracts a string value from kubectl jsonpath={.data} output which looks
// like map[key:value key2:value2]. This avoids importing encoding/json for a simple lookup.
func extractJSONField(data, key string) string {
	// kubectl jsonpath={.data} returns: map[key1:val1 key2:val2]
	// Find "key:" then grab the value up to the next space or ]
	search := key + ":"
	idx := strings.Index(data, search)
	if idx == -1 {
		return ""
	}
	rest := strings.TrimSpace(data[idx+len(search):])
	end := strings.IndexAny(rest, " ]")
	if end == -1 {
		return rest
	}
	return rest[:end]
}

// isSpecMutationExpected returns true when the upgrade path from→to is a known
// spec-mutating path. Add entries here when a Feast operator API change deliberately
// modifies existing CR specs or when the Helm chart intentionally diverges from the
// in-tree reconciler output for a specific version pair.
//
// Example (uncomment when a known mutation is introduced):
//
//	{"3.16", "3.17"},  // RHOAIENG-XXXXX: field renamed in modular migration
func isSpecMutationExpected(fromVersion, toVersion string) bool {
	knownMutations := [][2]string{
		// No known spec-mutating upgrade paths yet.
	}
	from := majorMinor(fromVersion)
	to := majorMinor(toVersion)
	if from == "" || to == "" {
		return false
	}
	for _, pair := range knownMutations {
		if pair[0] == from && pair[1] == to {
			return true
		}
	}
	return false
}

func majorMinor(version string) string {
	parts := strings.SplitN(version, ".", 3)
	if len(parts) < 2 {
		return ""
	}
	return parts[0] + "." + parts[1]
}
