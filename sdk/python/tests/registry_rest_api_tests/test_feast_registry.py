import json
import pytest
from support import *

EXPECTED_ENTITIES = {
    "dob_ssn": "STRING",
    "zipcode": "INT64",
}
credit_scoring_project = "credit_scoring_local"
driver_ranking_project = "driver_ranking"

class TestRegistryServerRest:

    @pytest.mark.openshift
    @pytest.mark.kind
    def test_list_entities(self,feast_rest_client):
        response = feast_rest_client.get(f"/entities/?project={credit_scoring_project}")
        assert response.status_code == 200

        data = response.json()
        entities = data["entities"]
        assert len(entities) == 3

    @pytest.mark.openshift
    @pytest.mark.kind
    def test_get_entity_by_name(self,feast_rest_client):
        response = feast_rest_client.get(f"/entities/zipcode/?project={credit_scoring_project}")
        assert response.status_code == 200
        data = response.json()
    
    @pytest.mark.openshift
    @pytest.mark.kind
    def test_list_data_sources(self,feast_rest_client):
        response=feast_rest_client.get(f"/data_sources/?project={credit_scoring_project}")
        assert response.status_code == 200
        data = response.json()
        data_sources =data["data_sources"]
        assert len(data_sources) == 3

    @pytest.mark.openshift
    @pytest.mark.kind
    def test_get_data_sources_name(self,feast_rest_client):
        response = feast_rest_client.get(f"/data_sources/Zipcode source/?project={credit_scoring_project}")
        assert response.status_code == 200
        data = response.json()

    @pytest.mark.openshift
    @pytest.mark.kind
    def test_list_feature_services(self,feast_rest_client):
        response = feast_rest_client.get(f"/feature_services/?project={driver_ranking_project}")
        assert response.status_code == 200

        data = response.json()
        feature_services = data.get("featureServices", [])

        assert len(feature_services) == 3, f"Expected 3 feature services, got {len(feature_services)}"

    @pytest.mark.openshift
    @pytest.mark.kind
    def test_get_feature_services_by_name(self,feast_rest_client):
        response = feast_rest_client.get(f"/feature_services/driver_activity_v2/?project={driver_ranking_project}")
        assert response.status_code == 200
        data = response.json()
        assert data["spec"]["name"] == "driver_activity_v2"

    @pytest.mark.openshift
    @pytest.mark.kind
    def test_list_feature_views(self,feast_rest_client):
        response = feast_rest_client.get(f"/feature_views/?project={credit_scoring_project}")
        assert response.status_code == 200

        data = response.json()
        assert isinstance(data["featureViews"], list)
        assert len(data["featureViews"]) > 0

    @pytest.mark.openshift
    def test_get_feature_view_by_name(self,feast_rest_client):
        response = feast_rest_client.get(f"/feature_views/credit_history/?project={credit_scoring_project}")
        assert response.status_code == 200
        data = response.json()
        assert "featureView" in data
        assert "spec" in data["featureView"]
        spec = data["featureView"]["spec"]
        assert spec["name"] == "credit_history"
        assert len(spec["features"]) > 0

    @pytest.mark.openshift
    @pytest.mark.kind
    def test_get_project_by_name(self,feast_rest_client):
        response = feast_rest_client.get(f"/projects/{credit_scoring_project}")
        assert response.status_code == 200
        data = response.json()
        assert data["spec"]["name"] == credit_scoring_project

    @pytest.mark.openshift
    def test_get_projects_list(self,feast_rest_client):
        response = feast_rest_client.get(f"/projects")
        assert response.status_code == 200
        data = response.json()
        assert len(data["projects"]) == 2
        for project in data["projects"]:
            assert project["spec"]["name"] in [credit_scoring_project, driver_ranking_project]

    @pytest.mark.openshift
    @pytest.mark.kind
    def test_get_registry_lineage(self,feast_rest_client):
        response = feast_rest_client.get(f"/lineage/registry?project={credit_scoring_project}")
        assert response.status_code == 200
        data = response.json()
