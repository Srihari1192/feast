import pytest
import requests
import subprocess
from support import *

class FeastRestClient:
    def __init__(self, base_url):
        self.base_url = base_url.rstrip("/")
        self.api_prefix = "/api/v1"

    def _build_url(self, endpoint):
        if not endpoint.startswith("/"):
            endpoint = "/" + endpoint
        return f"{self.base_url}{self.api_prefix}{endpoint}"
    
    def get(self, endpoint,params=None):
        if params is None:
            params = {}
        params.setdefault("allow_cache", "false")
        url = self._build_url(endpoint)
        return requests.get(url, params=params, verify=False)


@pytest.fixture(scope="session")
def feast_rest_client():
    # Load kube config
    config.load_kube_config()
    api_instance = client.CoreV1Api()
    
    namespace = "test-ns-feast-rest"
    feast_project = "credit-scoring"
    service_name = "feast-test-s3-registry-rest"
    ISRunOnOpenshift = os.getenv("RUN_ON_OPENSHIFT_CI", "false").lower() == "true"

    # setup
    create_namespace(api_instance,namespace)

    try:
        if not ISRunOnOpenshift:
            deploy_and_validate_pod(namespace,"resource/redis.yaml","app=redis")
            deploy_and_validate_pod(namespace, "resource/postgres.yaml","app=postgres")
            create_feast_project("resource/feast.yaml", namespace , feast_project)
            validate_feature_store_cr_status(namespace, feast_project)
            cron_job_cmd = [
            "create", "job", "--from=cronjob/feast-"+feast_project, "jobfeast",
            "-n", namespace
            ]
            run_kubectl_command(cron_job_cmd)
            route_url = create_route(namespace , feast_project, service_name)
        else:
            aws_access_key = os.getenv("AWS_ACCESS_KEY")
            aws_secret_key = os.getenv("AWS_SECRET_KEY")
            aws_bucket_name = os.getenv("AWS_BUCKET_NAME")
            aws_feast_registry_path = os.getenv("AWS_REGISTRY_FILE_PATH")
            run_kubectl_apply_with_sed(aws_access_key,aws_secret_key,aws_bucket_name,aws_feast_registry_path,"resource/feast_config_rhoai.yaml",namespace)
            validate_feature_store_cr_status(namespace, "test-s3")
            route_url = create_route(namespace , feast_project, service_name)
        if not route_url:
            raise RuntimeError("Route URL could not be fetched.")
        print(f"\n Connected to Feast REST at {route_url}")
        yield FeastRestClient(route_url)
    finally:
        print(f"\n[Teardown] Deleting namespace: {namespace}")
        delete_namespace(api_instance, namespace)
    