import logging

import pytest

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


class TestSearchAPIPagination:
    """Test class for pagination functionality in search API"""

    @pytest.fixture
    def pagination_responses(self, shared_search_responses, search_test_app):
        """Pre-computed pagination responses to reduce API calls"""
        return {
            "default": shared_search_responses["empty_query"],
            "page1_limit5": shared_search_responses["paginated_basic"],
            "page2_limit3": shared_search_responses["paginated_page2"],
            "large_limit": search_test_app.get(
                "/search?query=&page=1&limit=100"
            ).json(),
            "beyond_results": search_test_app.get(
                "/search?query=&page=999&limit=10"
            ).json(),
            "limit3": search_test_app.get("/search?query=&limit=3").json(),
        }

    def test_search_pagination_basic_functionality(self, pagination_responses):
        """Test basic pagination functionality using shared responses"""

        # Test default values (page=1, limit=50)
        default_data = pagination_responses["default"]
        assert "pagination" in default_data
        pagination = default_data["pagination"]
        assert pagination["page"] == 1
        assert pagination["limit"] == 50
        assert len(default_data["results"]) <= 50
        assert not pagination.get("hasPrevious", False)

        # Test page=1, limit=5
        page1_data = pagination_responses["page1_limit5"]
        pagination = page1_data["pagination"]
        assert pagination["page"] == 1
        assert pagination["limit"] == 5
        assert len(page1_data["results"]) <= 5
        assert not pagination.get("hasPrevious", False)

        # Test page=2, limit=3
        page2_data = pagination_responses["page2_limit3"]
        pagination = page2_data["pagination"]
        assert pagination["page"] == 2
        assert pagination["limit"] == 3
        assert len(page2_data["results"]) <= 3
        if pagination["totalCount"] > 3:
            assert pagination.get("hasPrevious", False)

        # Test large limit
        large_data = pagination_responses["large_limit"]
        pagination = large_data["pagination"]
        assert pagination["page"] == 1
        assert pagination["limit"] == 100
        assert len(large_data["results"]) <= pagination["totalCount"]

        # Test page beyond results
        beyond_data = pagination_responses["beyond_results"]
        pagination = beyond_data["pagination"]
        assert pagination["page"] == 999
        assert pagination["limit"] == 10
        assert len(beyond_data["results"]) == 0
        assert not pagination.get("hasNext", False)

    def test_search_pagination_metadata_comprehensive(
        self, pagination_responses, search_test_app
    ):
        """Comprehensive test for all pagination metadata accuracy using shared responses"""
        # Use limit=3 response for metadata testing
        data = pagination_responses["limit3"]
        total_count = data["pagination"]["totalCount"]
        total_pages = data["pagination"]["totalPages"]
        limit = data["pagination"]["limit"]

        # Verify total_pages calculation: (total + limit - 1) // limit
        expected_pages = (total_count + limit - 1) // limit
        assert total_pages == expected_pages

        # Test pagination metadata structure and types
        pagination = data["pagination"]
        assert isinstance(pagination["page"], int)
        assert isinstance(pagination["limit"], int)
        assert isinstance(pagination["totalCount"], int)
        assert isinstance(pagination["totalPages"], int)
        assert isinstance(pagination["hasNext"], bool)

        page = pagination["page"]
        limit = pagination["limit"]
        total = pagination["totalCount"]

        start = (page - 1) * limit
        end = start + limit

        assert not pagination.get("hasPrevious", False)  # First page has no previous
        expected_has_next = end < total
        assert pagination.get("hasNext", False) == expected_has_next

    @pytest.mark.parametrize(
        "sort_by,sort_order,query,limit",
        [
            ("name", "asc", "", 3),
            ("match_score", "desc", "user", 3),
            ("type", "asc", "", 5),
        ],
    )
    def test_search_pagination_with_sorting(
        self, search_test_app, sort_by, sort_order, query, limit
    ):
        """Test pagination with various sorting parameters"""
        response = search_test_app.get(
            f"/search?query={query}&page=1&limit={limit}&sort_by={sort_by}&sort_order={sort_order}"
        )
        assert response.status_code == 200

        data = response.json()
        results = data["results"]

        if len(results) > 1:
            # Verify results are sorted correctly
            for i in range(len(results) - 1):
                current_value = results[i].get(sort_by, "")
                next_value = results[i + 1].get(sort_by, "")

                if sort_order == "asc":
                    assert current_value <= next_value, (
                        f"Results not sorted ascending by {sort_by}"
                    )
                else:  # desc
                    assert current_value >= next_value, (
                        f"Results not sorted descending by {sort_by}"
                    )

        # Test sorting consistency across pages for name sorting
        if sort_by == "name" and sort_order == "asc":
            # Get second page to verify consistency
            page2_response = search_test_app.get(
                f"/search?query={query}&page=2&limit={limit}&sort_by={sort_by}&sort_order={sort_order}"
            )

            if page2_response.status_code == 200:
                page2_data = page2_response.json()
                page2_results = page2_data["results"]

                if len(results) > 0 and len(page2_results) > 0:
                    # Last item of page 1 should be <= first item of page 2
                    last_page1_name = results[-1]["name"]
                    first_page2_name = page2_results[0]["name"]
                    assert last_page1_name <= first_page2_name

    def test_search_pagination_with_filtering(self, search_test_app):
        """Test pagination with various filtering options"""
        # Test query filtering reduces total count
        response_all = search_test_app.get("/search?query=&limit=10")
        total_all = response_all.json()["pagination"]["totalCount"]

        response_filtered = search_test_app.get("/search?query=user&limit=10")
        total_filtered = response_filtered.json()["pagination"]["totalCount"]

        assert response_all.status_code == 200
        assert response_filtered.status_code == 200
        assert total_filtered <= total_all

        # Test project filtering
        response = search_test_app.get(
            "/search?query=&projects=test_project&page=1&limit=5"
        )
        assert response.status_code == 200

        data = response.json()
        assert "pagination" in data
        assert data["projects_searched"] == ["test_project"]

        # All results should be from test_project
        for result in data["results"]:
            if "project" in result:
                assert result["project"] == "test_project"

        # Test tag filtering
        response = search_test_app.get("/search?query=&tags=team:data&page=1&limit=3")
        assert response.status_code == 200

        data = response.json()
        assert "pagination" in data
        pagination = data["pagination"]
        assert pagination["page"] == 1
        assert pagination["limit"] == 3

        # Test empty results handling
        response = search_test_app.get(
            "/search?query=nonexistent_xyz_123&page=1&limit=10"
        )
        assert response.status_code == 200

        data = response.json()
        pagination = data["pagination"]

        assert not pagination.get("totalCount", False)
        assert not pagination.get("totalPages", False)
        assert not pagination.get("hasNext", False)
        assert not pagination.get("hasPrevious", False)
        assert len(data["results"]) == 0

    def test_search_pagination_boundary_conditions(self, search_test_app):
        """Comprehensive test for pagination boundary conditions and edge cases"""
        # Get total count for boundary calculations
        response = search_test_app.get("/search?query=")
        total_count = response.json()["pagination"]["totalCount"]

        # Test single result per page creates multiple pages
        response = search_test_app.get("/search?query=&page=1&limit=1")
        assert response.status_code == 200
        data = response.json()
        pagination = data["pagination"]

        assert pagination["limit"] == 1
        assert len(data["results"]) <= 1
        if pagination["totalCount"] > 1:
            assert pagination["totalPages"] == pagination["totalCount"]
            assert pagination["hasNext"]

        # Test exact page boundary (when total divisible by limit)
        if total_count >= 4:
            limit = 2 if total_count % 2 == 0 else 3 if total_count % 3 == 0 else 4
            if total_count % limit == 0:
                response = search_test_app.get(f"/search?query=&page=1&limit={limit}")
                data = response.json()
                pagination = data["pagination"]
                expected_pages = total_count // limit
                assert pagination["totalPages"] == expected_pages

        # Test off-by-one boundary conditions
        if total_count > 1:
            limit = total_count - 1
            response = search_test_app.get(f"/search?query=&page=1&limit={limit}")
            data = response.json()
            pagination = data["pagination"]
            assert pagination["totalPages"] == 2
            assert pagination["hasNext"]

        # Test mathematical accuracy of start/end calculations
        test_cases = [(1, 5), (2, 5), (3, 3)]
        for page, limit in test_cases:
            response = search_test_app.get(f"/search?query=&page={page}&limit={limit}")
            assert response.status_code == 200

            data = response.json()
            pagination = data["pagination"]

            expected_start = (page - 1) * limit
            expected_end = expected_start + limit

            assert pagination.get("hasPrevious", False) == (expected_start > 0)
            expected_has_next = expected_end < pagination["totalCount"]
            assert pagination["hasNext"] == expected_has_next

        # Test ceiling division for total pages calculation
        test_limits = [1, 2, 3, 5, 7, 10]
        for limit in test_limits:
            if limit <= total_count:
                response = search_test_app.get(f"/search?query=&limit={limit}")
                data = response.json()
                pagination = data["pagination"]
                expected_pages = (total_count + limit - 1) // limit
                assert pagination["totalPages"] == expected_pages

    def test_search_pagination_navigation_flags(
        self, search_test_app, shared_search_responses
    ):
        """Test has_next and has_previous flags accuracy across different pages"""
        # Test first page has no previous
        data = shared_search_responses["paginated_basic"]
        pagination = data["pagination"]
        assert not pagination.get("hasPrevious", False)
        assert pagination["page"] == 1
        total_pages = pagination.get("totalPages")

        if total_pages > 0:
            response = search_test_app.get(f"/search?query=&page={total_pages}&limit=5")
            data = response.json()
            pagination = data["pagination"]
            assert not pagination.get("hasNext", False)
            assert pagination["page"] == total_pages

        # Test empty results pagination
        response = search_test_app.get(
            "/search?query=impossible_nonexistent_query_xyz_999&page=1&limit=10"
        )
        assert response.status_code == 200
        data = response.json()
        pagination = data["pagination"]
        assert not pagination.get("totalCount", False)
        assert not pagination.get("totalPages", False)
        assert not pagination.get("hasNext", False)
        assert not pagination.get("hasPrevious", False)
        assert len(data["results"]) == 0

    def test_search_pagination_limit_above_maximum(self, search_test_app):
        """Test pagination limit above maximum allowed value (100) returns error"""
        response = search_test_app.get("/search?query=user&limit=150")
        assert response.status_code == 400

        error_data = response.json()
        assert "detail" in error_data
