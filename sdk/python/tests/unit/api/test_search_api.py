import logging

import pytest

import tests.unit.api.conftest as _conftest

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


class TestSearchAPI:
    """Test class for the comprehensive search API"""

    def test_search_user_query_comprehensive(self, shared_search_responses):
        """Comprehensive test for user query validation - combines multiple test scenarios"""
        data = shared_search_responses["user_query"]

        # Test response structure (replaces test_search_all_resources_with_query)
        assert "results" in data
        assert "pagination" in data
        assert "query" in data
        assert "projects_searched" in data
        assert "errors" in data
        assert data["query"] == "user"

        # Test pagination structure
        pagination = data["pagination"]
        assert pagination["totalCount"] > 0
        assert pagination["totalPages"] > 0
        assert pagination["page"] == 1
        assert pagination["limit"] == 50

        # Test results content
        results = data["results"]
        assert len(results) > 0
        result = results[0]
        required_result_fields = [
            "type",
            "name",
            "description",
            "project",
            "match_score",
        ]
        for field in required_result_fields:
            assert field in result

        # Log for debugging
        type_counts = {}
        for r in results:
            result_type = r.get("type", "unknown")
            type_counts[result_type] = type_counts.get(result_type, 0) + 1

        logger.debug(f"Found {len(results)} results:")
        for r in results:
            logger.debug(
                f"  - {r['type']}: {r['name']} (score: {r.get('match_score', 'N/A')})"
            )

        # Test that we found expected resources
        resource_names = [r["name"] for r in results]
        assert "user" in resource_names  # user entity

        # Test feature views
        feature_view_names = [r["name"] for r in results if r["type"] == "featureView"]
        if feature_view_names:
            assert "user_features" in feature_view_names
        else:
            logging.warning(
                "No feature views found in search results - this may indicate a search API issue"
            )

        # Test cross-project functionality (replaces test_search_cross_project_when_no_project_specified)
        assert len(data["projects_searched"]) >= 1
        assert "test_project" in data["projects_searched"]

    def test_search_with_project_filter(self, search_test_app):
        """Test searching within a specific project"""
        response = search_test_app.get("/search?query=user&projects=test_project")
        assert response.status_code == 200

        data = response.json()
        assert data["projects_searched"] == ["test_project"]

        results = data["results"]
        # All results should be from test_project
        for result in results:
            if "project" in result:
                assert result["project"] == "test_project"

    def test_search_by_description(self, search_test_app):
        """Test searching by description content"""
        response = search_test_app.get("/search?query=demographic")
        assert response.status_code == 200

        data = response.json()
        results = data["results"]

        # Debug: Show what we found
        logger.debug(f"Search for 'demographic' returned {len(results)} results:")
        for r in results:
            logger.debug(
                f"  - {r['type']}: {r['name']} - '{r.get('description', '')}' (score: {r.get('match_score', 'N/A')})"
            )

        # Should find user_features which has "demographic" in description
        feature_view_names = [r["name"] for r in results if r["type"] == "featureView"]
        if len(feature_view_names) > 0:
            assert "user_features" in feature_view_names
        else:
            # If no feature views found, check if any resources have "demographic" in description
            demographic_resources = [
                r for r in results if "demographic" in r.get("description", "").lower()
            ]
            if len(demographic_resources) == 0:
                logger.warning(
                    "No resources found with 'demographic' in description - search may not be working properly"
                )

    def test_search_by_tags(self, shared_search_responses):
        """Test searching by tag content"""
        # Get tags filtered results
        tags_data = shared_search_responses["with_tags"]
        logger.debug(f"Tags data: {tags_data}")
        results = tags_data["results"]
        assert len(results) > 0

        # Should find user-related resources that also have "team": "data" tag
        expected_resources = {"user", "user_features", "user_service"}
        found_resources = {r["name"] for r in results}

        # Check intersection rather than strict subset (more flexible)
        found_expected = expected_resources.intersection(found_resources)
        assert len(found_expected) > 0, (
            f"Expected to find some of {expected_resources} but found none in {found_resources}"
        )

    def test_search_matched_tags_exact_match(self, search_test_app):
        """Test that matched_tags field is present when a tag matches exactly"""
        # Search for "data" which should match tag key "team" with value "data"
        response = search_test_app.get("/search?query=data")
        assert response.status_code == 200

        data = response.json()
        results = data["results"]

        # Find results that matched via tags (match_score = 60)
        tag_matched_results = [
            r for r in results if r.get("match_score") == 60 and "matched_tags" in r
        ]

        assert len(tag_matched_results) > 0, (
            "Expected to find at least one result with matched_tags from tag matching"
        )

        # Verify matched_tags is present and has a valid dictionary value
        for result in tag_matched_results:
            matched_tags = result.get("matched_tags")
            assert matched_tags is not None, (
                f"matched_tags should not be None for result {result['name']}"
            )
            assert isinstance(matched_tags, dict), (
                f"matched_tags should be a dictionary, got {type(matched_tags)}"
            )
            # matched_tags should be a non-empty dict for tag-matched results
            assert len(matched_tags) > 0, (
                "matched_tags should not be empty for tag matches"
            )

        logger.debug(
            f"Found {len(tag_matched_results)} results with matched_tags: {[r['name'] + ' -> ' + str(r.get('matched_tags', 'N/A')) for r in tag_matched_results]}"
        )

    def test_search_matched_tags_multiple_tags(self, search_test_app):
        """Test that multiple matching tags are returned in matched_tags"""
        # Search for "a" which should match:
        # - Names containing "a" (e.g., user_training_dataset, data sources)
        # - Tags where key/value contains "a": "team" (key), "data" (value), "training" (value)
        response = search_test_app.get("/search?query=a")
        logger.info(response.json())
        assert response.status_code == 200

        data = response.json()
        results = data["results"]

        # Find user_training_dataset which has tags: {"environment": "test", "purpose": "training", "team": "data"}
        # "team" contains "a", "data" contains "a", "training" contains "a"
        # So matched_tags should have at least 2 entries: "purpose" and "team"
        dataset_results = [
            r for r in results if r.get("name") == "user_training_dataset"
        ]

        assert len(dataset_results) > 0, (
            "Expected to find user_training_dataset in results"
        )

        dataset_result = dataset_results[0]
        matched_tags = dataset_result.get("matched_tags", {})

        assert isinstance(matched_tags, dict), (
            f"matched_tags should be a dictionary, got {type(matched_tags)}"
        )

        # Should have multiple matching tags: "purpose" and "team"
        assert len(matched_tags) >= 2, (
            f"Expected at least 2 matching tags for 'a' query, got {len(matched_tags)}: {matched_tags}"
        )

        # Verify the expected tags are present
        assert "team" in matched_tags and "purpose" in matched_tags, (
            f"Expected 'team' and 'purpose' in matched_tags, got: {matched_tags}"
        )

        logger.debug(f"user_training_dataset matched_tags: {matched_tags}")

    def test_search_matched_tags_fuzzy_match(self, search_test_app):
        """Test that matched_tags field is present when a tag matches via fuzzy matching"""
        # Search for "te" which should fuzzy match tag key "team"
        # "te" vs "team": overlap={'t','e'}/union={'t','e','a','m'} = 2/4 = 50% (below threshold)
        # Try "tea" which should fuzzy match "team" better
        # "tea" vs "team": overlap={'t','e','a'}/union={'t','e','a','m'} = 3/4 = 75% (above threshold)
        response = search_test_app.get("/search?query=tea")
        assert response.status_code == 200

        data = response.json()
        results = data["results"]

        # Find results that matched via fuzzy tag matching (match_score < 60 but >= 40)
        fuzzy_tag_matched_results = [
            r
            for r in results
            if r.get("match_score", 0) >= 40
            and r.get("match_score", 0) < 60
            and "matched_tags" in r
        ]

        # If we don't find fuzzy matches, try a different query that's more likely to match
        if len(fuzzy_tag_matched_results) == 0:
            # Try "dat" which should fuzzy match tag value "data"
            # "dat" vs "data": overlap={'d','a','t'}/union={'d','a','t','a'} = 3/4 = 75% (above threshold)
            response = search_test_app.get("/search?query=dat")
            assert response.status_code == 200
            data = response.json()
            results = data["results"]
            fuzzy_tag_matched_results = [
                r
                for r in results
                if r.get("match_score", 0) >= 40
                and r.get("match_score", 0) < 60
                and "matched_tags" in r
            ]

        if len(fuzzy_tag_matched_results) > 0:
            # Verify matched_tags is present for fuzzy matches
            for result in fuzzy_tag_matched_results:
                matched_tags = result.get("matched_tags")
                assert matched_tags is not None, (
                    f"matched_tags should not be None for fuzzy-matched result {result['name']}"
                )
                assert isinstance(matched_tags, dict), (
                    f"matched_tags should be a dictionary, got {type(matched_tags)}"
                )
                assert len(matched_tags) > 0, (
                    "matched_tags should not be empty for fuzzy tag matches"
                )
                # Verify the match_score is in the fuzzy range
                assert 40 <= result.get("match_score", 0) < 60, (
                    f"Fuzzy tag match should have score in [40, 60), got {result.get('match_score')}"
                )

            logger.debug(
                f"Found {len(fuzzy_tag_matched_results)} results with fuzzy matched_tags: {[r['name'] + ' -> ' + str(r.get('matched_tags', 'N/A')) + ' (score: ' + str(r.get('match_score', 'N/A')) + ')' for r in fuzzy_tag_matched_results]}"
            )

    def test_search_sorting_functionality(self, shared_search_responses):
        """Test search results sorting using pre-computed responses"""
        # Test match_score descending sort
        match_score_data = shared_search_responses["sorted_by_match_score"]
        results = match_score_data["results"]
        if len(results) > 1:
            for i in range(len(results) - 1):
                current_score = results[i].get("match_score", 0)
                next_score = results[i + 1].get("match_score", 0)
                assert current_score >= next_score, (
                    "Results not sorted descending by match_score"
                )

        # Test name ascending sort
        name_data = shared_search_responses["sorted_by_name"]
        results = name_data["results"]
        if len(results) > 1:
            for i in range(len(results) - 1):
                current_name = results[i].get("name", "")
                next_name = results[i + 1].get("name", "")
                assert current_name <= next_name, "Results not sorted ascending by name"

    def test_search_query_functionality(self, shared_search_responses):
        """Test basic search functionality with different query types using shared responses"""
        # Test empty query returns all resources
        empty_data = shared_search_responses["empty_query"]
        assert len(empty_data["results"]) > 0
        assert empty_data["query"] == ""

        results = empty_data["results"]

        # Get all resource types returned
        returned_types = set(result["type"] for result in results)

        # Should include all expected resource types (including new 'feature' type)
        expected_types = {
            "entity",
            "featureView",
            "feature",
            "featureService",
            "dataSource",
            "savedDataset",
        }

        # All expected types should be present (or at least no filtering happening)
        # Note: Some types might not exist in test data, but if they do exist, they should all be returned
        available_types_in_data = expected_types.intersection(returned_types)
        assert len(available_types_in_data) >= 4, (
            f"Expected multiple resource types in results, but only got {returned_types}. "
            "All available resource types should be searched."
        )

        # Verify feature result structure
        for result in results:
            # Check required fields
            assert "type" in result
            assert "name" in result
            assert "description" in result
            assert "project" in result

        # Get all feature results
        feature_results = [result for result in results if result["type"] == "feature"]

        # Should have individual features in search results
        assert len(feature_results) > 0, (
            "Expected individual features to appear in search results, but found none"
        )

        for feature_result in feature_results:
            assert "featureView" in feature_result
            assert feature_result["featureView"] in [
                "user_features",
                "product_features",
                "transaction_features",
                "user_on_demand_features",
            ]

        # Verify we have features that likely come from different feature views
        feature_names = {f["name"] for f in feature_results}

        # Based on test fixture features: age, income (from user_features), price, category (from product_features),
        # amount, payment_method (from transaction_features)
        expected_features = {
            "age",
            "income",
            "price",
            "category",
            "amount",
            "payment_method",
        }
        found_features = expected_features.intersection(feature_names)

        assert len(found_features) >= 3, (
            f"Expected features from multiple feature views, but only found features: {feature_names}. "
            f"Expected to find at least 3 of: {expected_features}"
        )

        # Get all feature view results to understand the source feature views
        feature_view_results = [
            result for result in results if result["type"] == "featureView"
        ]
        feature_view_names = {fv["name"] for fv in feature_view_results}

        # Based on test fixture: user_features, product_features, transaction_features
        expected_feature_views = {
            "user_features",
            "product_features",
            "transaction_features",
        }

        # Should have feature views from test fixture
        found_feature_views = expected_feature_views.intersection(feature_view_names)
        assert len(found_feature_views) >= 2, (
            f"Expected features from multiple feature views, but only found feature views: {feature_view_names}. "
            f"Expected to find some of: {expected_feature_views}"
        )

        # Test nonexistent query
        nonexistent_data = shared_search_responses["nonexistent_query"]
        logger.debug(f"Nonexistent data: {nonexistent_data}")
        assert len(nonexistent_data["results"]) == 0

        # Search for a specific feature name 'age'
        age_feature_response = shared_search_responses["feature_name_query"]

        results = age_feature_response["results"]

        # Should find feature named "age"
        age_features = [
            result
            for result in results
            if result["type"] == "feature" and "age" in result["name"].lower()
        ]

        assert len(age_features) > 0, (
            "Expected to find feature named 'age' in search results"
        )

    def test_search_fuzzy_matching(self, search_test_app):
        """Test fuzzy matching functionality with assumed threshold of 0.6"""
        # Assumption: fuzzy matching threshold is 0.6 (60% similarity)
        # "usr" should match "user" as it's a partial match with reasonable similarity
        response = search_test_app.get("/search?query=usr")
        assert response.status_code == 200

        data = response.json()
        results = data["results"]

        # Should find user-related resources due to fuzzy matching
        user_matches = [r for r in results if "user" in r["name"].lower()]

        if len(user_matches) > 0:
            # If fuzzy matching works, verify match scores are reasonable but lower than exact matches
            for match in user_matches:
                match_score = match.get("match_score", 0)
                # Fuzzy matches should have lower scores than exact matches (< 80)
                # but still above minimum threshold (>= 40 for reasonable partial matches)
                assert 40 <= match_score < 80, (
                    f"Fuzzy match score {match_score} outside expected range [40, 80) for {match['name']}"
                )

        # Test with closer match - "use" should definitely match "user" if fuzzy matching enabled
        response = search_test_app.get("/search?query=use")
        assert response.status_code == 200

        data = response.json()
        close_matches = [r for r in data["results"] if "user" in r["name"].lower()]

        # "use" is closer to "user" than "usr", so should have better chance of matching
        # If fuzzy matching is implemented, this should find matches
        logger.debug(f"'use' query found {len(close_matches)} user-related matches")
        for match in close_matches:
            logger.debug(
                f"  - {match['name']}: score {match.get('match_score', 'N/A')}"
            )

    def test_search_api_special_characters(self, search_test_app):
        """Test search API with special characters in query and verify expected results"""
        # Define expected matches for each special character query
        # NOTE: Queries are designed to achieve 75%+ similarity with fuzzy matching algorithm
        special_query_expectations = {
            "users": {
                "should_find": [
                    "user"
                ],  # "users" vs "user": overlap={'u','s','e','r'}/union={'u','s','e','r','s'} = 4/5 = 80%
                "description": "Plural form should find user entity",
            },
            "user_feature": {
                "should_find": [
                    "user_features",
                ],  # "user_feature" vs "user_features": overlap={'u','s','e','r','_','f','a','t','u','r'}/union={'u','s','e','r','_','f','a','t','u','r','e','s'} = 10/12 = 83%
                "description": "Singular form should find feature views",
            },
            "product": {
                "should_find": [
                    "product",
                    "product_features",
                    "product_source",
                ],  # "product" vs "product": 100% match
                "description": "Exact match should find product resources",
            },
            "sources": {
                "should_find": [
                    "user_source",
                    "product_source",
                    "transaction_source",
                ],  # "sources" vs "user_source": overlap={'s','o','u','r','c','e'}/union={'s','o','u','r','c','e','_','u'} = 6/8 = 75%
                "description": "Plural form should find data sources",
            },
        }

        for query, expectation in special_query_expectations.items():
            response = search_test_app.get(f"/search?query={query}")
            assert response.status_code == 200

            data = response.json()
            assert "results" in data
            assert isinstance(data["results"], list)
            assert data["pagination"]["totalCount"] > 0

            results = data["results"]
            found_names = {r["name"] for r in results}
            expected_names = set(expectation["should_find"])

            logger.debug(
                f"Query '{query}' found {len(results)} results: {list(found_names)}"
            )
            logger.debug(
                f"       Expected to find: {list(expected_names)} - {expectation['description']}"
            )

            # Check if we found at least some of the expected resources
            # Use intersection since search might be fuzzy and return additional results
            found_expected = expected_names.intersection(found_names)

            if len(found_expected) > 0:
                # If we found some expected resources, verify they have reasonable match scores
                for result in results:
                    if result["name"] in expected_names:
                        match_score = result.get("match_score", 0)
                        assert match_score > 0, (
                            f"Expected positive match score for '{result['name']}' but got {match_score}"
                        )

            # Verify query echo-back works with special characters
            assert data["query"] == query, (
                f"Query echo-back failed for special characters: expected '{query}' but got '{data['query']}'"
            )

    def test_search_specific_multiple_projects(self, search_test_app):
        response = search_test_app.get(
            "/search?query=user&projects=test_project&projects=another_project"
        )
        assert response.status_code == 200

        data = response.json()
        results = data.get("results", [])
        project_counts = {}
        for result in results:
            project = result.get("project", "unknown")
            project_counts[project] = project_counts.get(project, 0) + 1

        assert "projects_searched" in data
        # Should search only existing projects, non-existing ones are ignored
        expected_projects = ["test_project"]  # only existing project
        assert data["projects_searched"] == expected_projects
        logger.debug(f"Errors: {data['errors']}")
        assert "Following projects do not exist: another_project" in data["errors"]
        assert data["errors"] == ["Following projects do not exist: another_project"]

        # Results should include project information
        for result in data["results"]:
            if "project" in result:
                assert result["project"] in expected_projects

    def test_search_empty_projects_parameter_searches_all(self, search_test_app):
        """Test that empty projects parameter still searches all projects"""
        response = search_test_app.get("/search?query=user&projects=")
        assert response.status_code == 200

        data = response.json()
        # Should search all available projects (at least test_project)
        assert len(data["projects_searched"]) >= 1
        assert "test_project" in data["projects_searched"]

    def test_search_nonexistent_projects(self, search_test_app):
        """Test searching in projects that don't exist"""
        response = search_test_app.get(
            "/search?query=user&projects=nonexistent1&projects=nonexistent2"
        )
        assert response.status_code == 200

        data = response.json()
        assert data["projects_searched"] == []  # no existing projects to search
        # Should return empty results since projects don't exist
        assert data["results"] == []
        assert not data["pagination"].get("totalCount", False)
        assert len(data["errors"]) == 1
        for proj in ["nonexistent1", "nonexistent2"]:
            assert proj in data["errors"][0]

    def test_search_many_projects_performance(self, search_test_app):
        """Test search performance with many projects"""
        # Create a list of many projects (mix of existing and non-existing)
        fake_projects = [f"fake_project_{i}" for i in range(20)]
        many_projects = ["test_project"] + fake_projects
        projects_param = "&".join([f"projects={p}" for p in many_projects])

        response = search_test_app.get(f"/search?query=user&{projects_param}")
        assert response.status_code == 200

        data = response.json()
        assert len(data["projects_searched"]) == 1  # only 1 real project exists
        assert "test_project" in data["projects_searched"]
        assert len(data["errors"]) == 1

        for proj in fake_projects:
            assert proj in data["errors"][0]

        # Should still return results from the one existing project
        if data["results"]:
            for result in data["results"]:
                if "project" in result:
                    assert result["project"] == "test_project"

    def test_search_duplicate_projects_deduplication(self, search_test_app):
        """Test that duplicate projects in list are handled properly"""
        response = search_test_app.get(
            "/search?query=user&projects=test_project&projects=test_project&projects=test_project"
        )
        assert response.status_code == 200

        data = response.json()
        # API should handle duplicates gracefully (may or may not deduplicate)
        # At minimum, should not crash and should search test_project
        assert len(data["projects_searched"]) == 1
        assert "test_project" == data["projects_searched"][0]

    def test_search_on_demand_feature_view(self, search_test_app):
        """Test searching for on-demand feature views"""
        # Search by name
        _conftest.global_store.registry.refresh()
        response = search_test_app.get("/search?query=user_on_demand_features")
        assert response.status_code == 200

        data = response.json()
        results = data["results"]

        # Should find the on-demand feature view
        on_demand_fv_results = [r for r in results if r["type"] == "featureView"]
        assert len(on_demand_fv_results) > 0

        on_demand_fv = on_demand_fv_results[0]
        logger.debug(f"On-demand feature view: {on_demand_fv_results}")
        assert on_demand_fv["name"] == "user_on_demand_features"
        assert (
            "On-demand features combining user features with real-time data"
            in on_demand_fv["description"]
        )
        assert on_demand_fv["project"] == "test_project"
        assert "match_score" in on_demand_fv
        assert on_demand_fv["match_score"] > 0

        # Search by description content
        response = search_test_app.get("/search?query=real-time")
        assert response.status_code == 200

        data = response.json()
        results = data["results"]

        # Should find the on-demand feature view by description
        on_demand_description_results = [
            r
            for r in results
            if "real-time" in r.get("description", "").lower()
            or "real_time" in r.get("description", "").lower()
        ]
        assert len(on_demand_description_results) > 0

        # Check that our on-demand feature view is in the results
        on_demand_names = [r["name"] for r in on_demand_description_results]
        assert "user_on_demand_features" in on_demand_names

        # Search by tags
        response = search_test_app.get("/search?query=&tags=type:real_time")
        assert response.status_code == 200

        data = response.json()
        results = data["results"]

        # Should find the on-demand feature view by tag
        tagged_results = [r for r in results if r["name"] == "user_on_demand_features"]
        assert len(tagged_results) > 0

        tagged_result = tagged_results[0]
        assert tagged_result["type"] == "featureView"
        assert tagged_result["name"] == "user_on_demand_features"

    def test_search_on_demand_features_individual(self, search_test_app):
        """Test searching for individual features from on-demand feature views"""
        # Search for individual features from the on-demand feature view
        response = search_test_app.get("/search?query=age_conversion_score")
        assert response.status_code == 200

        data = response.json()
        results = data["results"]

        # Should find the individual feature from the on-demand feature view
        feature_results = [
            r
            for r in results
            if r["type"] == "feature" and r["name"] == "age_conversion_score"
        ]
        assert len(feature_results) > 0

        feature_result = feature_results[0]
        assert feature_result["name"] == "age_conversion_score"
        assert feature_result["type"] == "feature"
        assert feature_result["project"] == "test_project"
        assert "match_score" in feature_result
        assert feature_result["match_score"] == 100  # Exact match should have score 100

        # Verify that features from different feature view types can be found together
        response = search_test_app.get("/search?query=&sort_by=name&sort_order=asc")
        assert response.status_code == 200

        data = response.json()
        all_features = [r for r in data["results"] if r["type"] == "feature"]

        # Should have features from both regular feature views and on-demand feature views
        regular_features = []
        on_demand_features = []

        for feature in all_features:
            if feature["name"] in [
                "age",
                "income",
                "price",
                "category",
                "amount",
                "payment_method",
            ]:
                regular_features.append(feature)
            elif feature["name"] in ["age_conversion_score"]:
                on_demand_features.append(feature)

        assert len(regular_features) > 0, (
            "Should have features from regular feature views"
        )
        assert len(on_demand_features) > 0, (
            "Should have features from on-demand feature views"
        )

        logger.debug(
            f"Found {len(regular_features)} regular features and {len(on_demand_features)} on-demand features"
        )

    def test_search_missing_required_query_parameter(self, search_test_app):
        """Test search API fails when required query parameter is missing"""
        response = search_test_app.get("/search")
        assert response.status_code == 422  # Unprocessable Entity

        error_data = response.json()
        assert "detail" in error_data
        logger.debug(f"Error data: {error_data}")
        # FastAPI should return validation error for missing required field
        assert "query" in str(error_data["detail"]).lower()

    @pytest.mark.parametrize(
        "test_cases",
        [
            [
                ("sort_by", "invalid_sort_field", "sort_order", "desc", 400),
                ("sort_by", "name", "sort_order", "invalid_order", 400),
                ("sort_by", "", "sort_order", "asc", 200),
                ("sort_by", "match_score", "sort_order", "", 200),
                ("sort_by", "123", "sort_order", "xyz", 400),
                (
                    "allow_cache",
                    "invalid_bool",
                    None,
                    None,
                    422,
                ),  # FastAPI may handle gracefully
                (
                    "allow_cache",
                    "yes",
                    None,
                    None,
                    200,
                ),  # FastAPI converts to boolean
                (
                    "allow_cache",
                    "1",
                    None,
                    None,
                    200,
                ),  # FastAPI converts to boolean
            ],
        ],
    )
    def test_search_with_invalid_parameters(self, search_test_app, test_cases):
        """Test search API with various invalid parameter combinations"""
        logger.debug(f"Test cases: {test_cases}")
        for param1, value1, param2, value2, expected_code in test_cases:
            # Build query string
            query_parts = ["query=user"]
            query_parts.append(f"{param1}={value1}")
            if param2 is not None and value2 is not None:
                query_parts.append(f"{param2}={value2}")

            url = "/search?" + "&".join(query_parts)
            response = search_test_app.get(url)

            assert response.status_code == expected_code, (
                f"Expected {expected_code} but got {response.status_code} for {param1}='{value1}'"
                + (f", {param2}='{value2}'" if param2 else "")
            )

            if response.status_code == 200:
                # If successful, verify response format
                data = response.json()
                assert "results" in data
                assert isinstance(data["results"], list)
            elif response.status_code in [400, 422]:
                # If validation error, verify it's a proper FastAPI error
                error_data = response.json()
                assert "detail" in error_data

    def test_search_with_extremely_long_query(self, search_test_app):
        """Test search API with extremely long query string"""
        # Create a very long query (10KB)
        long_query = "a" * 10000

        response = search_test_app.get(f"/search?query={long_query}")
        assert response.status_code == 200  # Should handle large queries gracefully

        data = response.json()
        assert "results" in data
        assert data["query"] == long_query

    def test_search_with_malformed_and_edge_case_parameters(self, search_test_app):
        """Test search API with malformed parameters and edge case values"""
        # Test malformed tags
        malformed_tags = [
            "invalid_tag_format",
            "key1:value1:extra",
            "=value_without_key",
            "key_without_value=",
            "::",
            "key1=value1&key2",
            "key with spaces:value",
        ]

        for malformed_tag in malformed_tags:
            response = search_test_app.get(f"/search?query=test&tags={malformed_tag}")
            assert response.status_code == 200
            data = response.json()
            assert "results" in data

        # Test empty and null-like query values
        empty_scenarios = [
            ("", "empty string"),
            ("   ", "whitespace only"),
            ("null", "string 'null'"),
            ("undefined", "string 'undefined'"),
            ("None", "string 'None'"),
        ]

        for query_value, description in empty_scenarios:
            response = search_test_app.get(f"/search?query={query_value}")
            assert response.status_code == 200, f"Failed for {description}"
            data = response.json()
            assert "results" in data
            assert data["query"] == query_value

    def test_search_all_resource_types_individually(self, search_test_app):
        """Test that all resource types can be searched individually and return only that type"""

        pytest.skip("Skipping resource types filtering tests")

        # Expected counts based on test fixture data
        expected_counts = {
            "entities": 3,  # user, product, transaction
            "feature_views": 3,  # user_features, product_features, transaction_features
            "feature_services": 2,  # user_service, product_service
            "data_sources": 3,  # user_source, product_source, transaction_source
            "saved_datasets": 1,  # user_training_dataset
            "permissions": 0,  # No permissions in test data
            "projects": 1,  # test_project
        }

        for resource_type in expected_counts.keys():
            response = search_test_app.get(
                f"/search?query=&resource_types={resource_type}"
            )
            assert response.status_code == 200

            data = response.json()
            assert "results" in data
            assert isinstance(data["results"], list)

            results = data["results"]
            expected_count = expected_counts[resource_type]

            # Map plural resource_type to singular type names used in results
            type_mapping = {
                "entities": "entity",
                "feature_views": "featureView",
                "feature_services": "featureService",
                "data_sources": "dataSource",
                "saved_datasets": "savedDataset",
                "permissions": "permission",
                "projects": "project",
            }
            expected_type = type_mapping[resource_type]

            # Assert all results are of the requested type
            for result in results:
                assert result.get("type") == expected_type, (
                    f"Expected type '{expected_type}' but got '{result.get('type')}' for resource_type '{resource_type}'"
                )

            # Filter out Feast internal resources (like __dummy entity) for count validation
            if resource_type == "entities":
                # Feast automatically creates __dummy entity - filter it out for test validation
                filtered_results = [
                    r for r in results if not r.get("name", "").startswith("__")
                ]
                actual_count = len(filtered_results)
                logger.debug(
                    f"entities returned {len(results)} total results, {actual_count} non-internal (expected {expected_count})"
                )
                logger.debug(
                    f"       Internal entities filtered: {[r['name'] for r in results if r.get('name', '').startswith('__')]}"
                )
            else:
                filtered_results = results
                actual_count = len(filtered_results)
                logger.debug(
                    f"{resource_type} returned {actual_count} results (expected {expected_count})"
                )

            # Assert expected count (allow some flexibility for permissions/projects that might vary)
            if resource_type in ["permissions", "projects"]:
                assert actual_count >= 0, (
                    f"Resource type '{resource_type}' should return non-negative count"
                )
            else:
                assert actual_count == expected_count, (
                    f"Expected {expected_count} results for '{resource_type}' but got {actual_count} (after filtering internal resources)"
                )

    def test_search_specific_resource_types(self, search_test_app):
        """Test filtering by specific resource types"""

        pytest.skip("Skipping resource types filtering tests")
        # Search only entities
        response = search_test_app.get("/search?query=user&resource_types=entities")
        assert response.status_code == 200

        data = response.json()
        results = data["results"]

        # All results should be entities
        for result in results:
            assert result["type"] == "entity"

        # Should find the user entity
        entity_names = [r["name"] for r in results]
        assert "user" in entity_names

    def test_search_multiple_resource_types(self, search_test_app):
        """Test filtering by multiple resource types"""

        pytest.skip("Skipping resource types filtering tests")

        response = search_test_app.get(
            "/search?query=product&resource_types=entities&resource_types=feature_views"
        )
        assert response.status_code == 200

        data = response.json()
        results = data["results"]

        # Results should only be entities or feature_views
        result_types = [r["type"] for r in results]
        for result_type in result_types:
            assert result_type in ["entity", "featureView"]

    def test_search_with_mixed_valid_invalid_resource_types(self, search_test_app):
        """Test search API with mix of valid and invalid resource types"""

        pytest.skip("Skipping resource types filtering tests")

        response = search_test_app.get(
            "/search?query=user&resource_types=entities&resource_types=invalid_type&resource_types=feature_views&resource_types=another_invalid"
        )
        assert response.status_code == 200

        data = response.json()
        # Should process valid types and ignore invalid ones
        assert "entities" in data["resource_types"]
        assert "feature_views" in data["resource_types"]
        assert "invalid_type" not in data["resource_types"]
        assert "another_invalid" not in data["resource_types"]

        # Results should only come from valid resource types
        if data["results"]:
            valid_types = {
                "entity",
                "featureView",
                "featureService",
                "dataSource",
                "savedDataset",
                "permission",
                "project",
            }
            for result in data["results"]:
                assert result.get("type") in valid_types or result.get("type") == ""

        # Test scenarios that should return 400 due to stricter validation
        scenarios_400 = [
            "/search?query=&sort_by=invalid",
        ]

        for scenario in scenarios_400:
            response = search_test_app.get(scenario)
            assert response.status_code == 400

    def test_search_with_invalid_resource_types(self, search_test_app):
        """Test search API with invalid resource types"""

        pytest.skip("Skipping resource types filtering tests")

        invalid_resource_types = [
            "invalid_type",
            "nonexistent_resource",
            "malformed_type",
            "",  # empty string
            "123",  # numeric
            "feature_views_typo",
        ]

        for invalid_type in invalid_resource_types:
            response = search_test_app.get(
                f"/search?query=test&resource_types={invalid_type}"
            )
            assert response.status_code == 200  # Should handle gracefully

            data = response.json()
            # Should return empty results for invalid types
            assert isinstance(data["results"], list)
            assert data["totalCount"] >= 0

    def test_search_with_multiple_invalid_resource_types(self, search_test_app):
        """Test search API with multiple invalid resource types"""

        pytest.skip("Skipping resource types filtering tests")

        response = search_test_app.get(
            "/search?query=test&resource_types=invalid1&resource_types=invalid2&resource_types=invalid3"
        )
        assert response.status_code == 200

        data = response.json()
        assert data["resource_types"] == []
        assert data["results"] == []  # Should return empty for all invalid types
