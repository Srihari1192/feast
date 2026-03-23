import logging

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


class TestSearchAPIMultiProjectComprehensive:
    """Comprehensive test class for multi-project search functionality with overlapping resource names"""

    def test_search_across_all_projects_with_overlapping_names(
        self, multi_project_search_test_app
    ):
        """Test searching across all projects when resources have overlapping names"""
        response = multi_project_search_test_app.get("/search?query=user")
        assert response.status_code == 200

        data = response.json()

        # Should find resources from multiple projects
        projects_found = set()
        user_entities = []
        user_features = []
        user_services = []

        for result in data["results"]:
            if "project" in result:
                projects_found.add(result["project"])

            # Collect user-related resources
            if "user" in result.get("name", "").lower():
                if result["type"] == "entity":
                    user_entities.append(result)
                elif result["type"] == "featureView":
                    user_features.append(result)
                elif result["type"] == "featureService":
                    user_services.append(result)

        # Should find resources from project_a and project_b (both have 'user' entities/features)
        assert len(projects_found) >= 2
        assert "project_a" in projects_found
        assert "project_b" in projects_found

        # Should find user entities from both projects with same name but different descriptions
        assert len(user_entities) >= 2
        descriptions = [entity["description"] for entity in user_entities]
        assert any("ride sharing" in desc for desc in descriptions)
        assert any("food delivery" in desc for desc in descriptions)

        # Should find user_features from both projects with same name but different contexts
        assert len(user_features) >= 2
        feature_descriptions = [fv["description"] for fv in user_features]
        assert any("rides" in desc for desc in feature_descriptions)
        assert any("food" in desc for desc in feature_descriptions)

    def test_search_specific_multiple_projects_with_same_resource_names(
        self, multi_project_search_test_app
    ):
        """Test searching in specific projects that have resources with same names"""
        response = multi_project_search_test_app.get(
            "/search?query=user_features&projects=project_a&projects=project_b"
        )
        assert response.status_code == 200

        data = response.json()
        for proj in ["project_a", "project_b"]:
            assert proj in data["projects_searched"]

        # Should find user_features from both specified projects
        user_features_results = [
            r for r in data["results"] if r["name"] == "user_features"
        ]
        assert len(user_features_results) == 2

        # Verify both projects are represented
        projects_in_results = {r["project"] for r in user_features_results}
        assert projects_in_results == {"project_a", "project_b"}

        # Verify different descriptions show they're different resources
        descriptions = {r["description"] for r in user_features_results}
        assert len(descriptions) == 2  # Should have different descriptions

    def test_search_by_domain_tags_across_projects(self, multi_project_search_test_app):
        """Test searching by domain-specific tags across projects"""
        response = multi_project_search_test_app.get("/search?query=transportation")
        assert response.status_code == 200

        data = response.json()

        tag_match_score = 60

        # Should only find resources from project_a (transportation domain)
        project_a_results = [
            r
            for r in data["results"]
            if r.get("project") == "project_a"
            and r.get("match_score") == tag_match_score
        ]

        assert len(project_a_results) > 0
        # Transportation should be specific to project_a based on our test data

        # Test food delivery domain
        response = multi_project_search_test_app.get("/search?query=food_delivery")
        assert response.status_code == 200

        data = response.json()
        project_b_results = [
            r for r in data["results"] if r.get("project") == "project_b"
        ]
        assert len(project_b_results) > 0

    def test_search_common_resource_names_different_contexts(
        self, multi_project_search_test_app
    ):
        """Test searching for resources that have same names but serve different purposes"""
        # Search for "common_analytics" data source which exists in both project_a and project_b
        response = multi_project_search_test_app.get("/search?query=common_analytics")
        assert response.status_code == 200

        data = response.json()

        # Look for unique common_analytics data sources (now prefixed with project names)
        common_analytics_results = [
            r for r in data["results"] if "common_analytics" in r.get("name", "")
        ]

        # Should find project_a_common_analytics and project_b_common_analytics
        project_a_analytics = [
            r
            for r in common_analytics_results
            if r.get("name") == "project_a_common_analytics"
        ]
        project_b_analytics = [
            r
            for r in common_analytics_results
            if r.get("name") == "project_b_common_analytics"
        ]

        assert len(project_a_analytics) == 1, (
            f"Expected 1 project_a_common_analytics, found {len(project_a_analytics)}"
        )
        assert len(project_b_analytics) == 1, (
            f"Expected 1 project_b_common_analytics, found {len(project_b_analytics)}"
        )
        assert len(common_analytics_results) >= 2

        # Should find results from both project_a and project_b
        projects_with_common = {
            r["project"] for r in common_analytics_results if "project" in r
        }
        assert "project_a" in projects_with_common
        assert "project_b" in projects_with_common

    def test_search_unique_resources_by_project(self, multi_project_search_test_app):
        """Test searching for resources that are unique to specific projects"""
        # Search for "restaurant" which should only exist in project_b
        response = multi_project_search_test_app.get("/search?query=restaurant")
        assert response.status_code == 200

        data = response.json()

        restaurant_results = [
            r for r in data["results"] if "restaurant" in r.get("name", "").lower()
        ]
        assert len(restaurant_results) > 0

        # All restaurant results should be from project_b
        for result in restaurant_results:
            if "project" in result:
                assert result["project"] == "project_b"

        # Search for "trip" which should only exist in project_a
        response = multi_project_search_test_app.get("/search?query=trip")
        assert response.status_code == 200

        data = response.json()

        trip_results = [
            r for r in data["results"] if "trip" in r.get("name", "").lower()
        ]
        assert len(trip_results) > 0

        # All trip results should be from project_a
        for result in trip_results:
            if "project" in result:
                assert result["project"] == "project_a"

    def test_search_project_isolation_verification(self, multi_project_search_test_app):
        """Test that project-specific searches properly isolate results"""
        # Search only in project_c
        response = multi_project_search_test_app.get(
            "/search?query=&projects=project_c"
        )
        assert response.status_code == 200

        data = response.json()
        assert data["projects_searched"] == ["project_c"]

        # All results should be from project_c
        for result in data["results"]:
            if "project" in result:
                assert result["project"] == "project_c", (
                    f"Found {result['type']} '{result['name']}' from project '{result['project']}' instead of 'project_c'"
                )

    def test_search_cross_project_resource_comparison(
        self, multi_project_search_test_app
    ):
        """Test comparing same-named resources across different projects"""
        # Search for user_service across projects
        response = multi_project_search_test_app.get("/search?query=user_service")
        assert response.status_code == 200

        data = response.json()

        user_service_results = [
            r for r in data["results"] if r["name"] == "user_service"
        ]
        assert len(user_service_results) >= 2

        # Group by project
        services_by_project = {}
        for service in user_service_results:
            project = service.get("project")
            if project:
                services_by_project[project] = service

        # Should have user_service in both project_a and project_b
        assert "project_a" in services_by_project
        assert "project_b" in services_by_project

        # Verify they have different descriptions (different contexts)
        desc_a = services_by_project["project_a"]["description"]
        desc_b = services_by_project["project_b"]["description"]
        assert desc_a != desc_b
        assert "ride sharing" in desc_a
        assert "food delivery" in desc_b

    def test_search_feature_view_entity_relationships_across_projects(
        self, multi_project_search_test_app
    ):
        """Test that feature views maintain proper entity relationships within each project"""
        response = multi_project_search_test_app.get(
            "/search?query=features&resource_types=feature_views"
        )
        assert response.status_code == 200

        data = response.json()

        # Group feature views by project
        fvs_by_project = {}
        for result in data["results"]:
            if result["type"] == "featureView":
                project = result.get("project")
                if project:
                    if project not in fvs_by_project:
                        fvs_by_project[project] = []
                    fvs_by_project[project].append(result)

        # Each project should have its own feature views
        assert len(fvs_by_project) >= 3

        # Verify project-specific feature views exist
        assert "project_a" in fvs_by_project
        assert "project_b" in fvs_by_project
        assert "project_c" in fvs_by_project

        # Each project should have feature views (project_c only has 1 with "features" in the name)
        for project, fvs in fvs_by_project.items():
            if project == "project_c":
                assert len(fvs) >= 1  # Only sales_features contains "features"
            else:
                assert (
                    len(fvs) >= 2
                )  # project_a and project_b have multiple with "features"

    def test_search_empty_query_cross_project_enumeration(
        self, multi_project_search_test_app
    ):
        """Test empty query returns resources from all projects properly enumerated"""
        response = multi_project_search_test_app.get("/search?query=")
        assert response.status_code == 200

        data = response.json()

        # Should find resources from all three projects
        projects_found = set()
        resource_counts_by_project = {}
        resource_types_by_project = {}

        for result in data["results"]:
            project = result.get("project")
            if project:
                projects_found.add(project)

                # Count resources per project
                if project not in resource_counts_by_project:
                    resource_counts_by_project[project] = 0
                resource_counts_by_project[project] += 1

                # Track resource types per project
                if project not in resource_types_by_project:
                    resource_types_by_project[project] = set()
                resource_types_by_project[project].add(result["type"])

        # Should find all three projects
        assert projects_found == {"project_a", "project_b", "project_c"}

        # Each project should have multiple resources
        for project, count in resource_counts_by_project.items():
            assert count >= 6  # At least entities + feature_views + feature_services

        # Each project should have multiple resource types
        for project, types in resource_types_by_project.items():
            expected_types = {
                "entity",
                "featureView",
                "featureService",
                "dataSource",
                "savedDataset",
                "feature",
            }
            # Should have at least some of the expected types
            assert len(expected_types.intersection(types)) >= 3

    def test_search_project_specific_with_nonexistent_projects(
        self, multi_project_search_test_app
    ):
        """Test searching with mix of existing and non-existing projects"""
        response = multi_project_search_test_app.get(
            "/search?query=user&projects=project_a&projects=nonexistent_project&projects=project_b"
        )
        assert response.status_code == 200

        data = response.json()
        assert len(data["errors"]) == 1
        assert "nonexistent_project" in data["errors"][0]

        for proj in ["project_a", "project_b"]:
            assert proj in data["projects_searched"]

        # Should only find results from existing projects
        projects_with_results = set()
        for result in data["results"]:
            if "project" in result:
                projects_with_results.add(result["project"])

        assert projects_with_results.issubset({"project_a", "project_b"})
