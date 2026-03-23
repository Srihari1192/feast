import logging
import os
import tempfile

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from feast import Entity, FeatureService, FeatureView, Field, FileSource, RequestSource
from feast.api.registry.rest.rest_registry_server import RestRegistryServer
from feast.feature_store import FeatureStore
from feast.infra.offline_stores.file_source import SavedDatasetFileStorage
from feast.on_demand_feature_view import on_demand_feature_view
from feast.project import Project
from feast.repo_config import RepoConfig
from feast.saved_dataset import SavedDataset
from feast.types import Float64, Int64, String
from feast.value_type import ValueType

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

global_store = None


@pytest.fixture
def search_test_app():
    """Test fixture that sets up a Feast environment with multiple resources for search testing"""
    # Create temp registry and data directory
    tmp_dir = tempfile.TemporaryDirectory()
    registry_path = os.path.join(tmp_dir.name, "registry.db")

    # Create dummy parquet files for different data sources
    user_data_path = os.path.join(tmp_dir.name, "user_data.parquet")
    product_data_path = os.path.join(tmp_dir.name, "product_data.parquet")
    transaction_data_path = os.path.join(tmp_dir.name, "transaction_data.parquet")

    # Create user data
    user_df = pd.DataFrame(
        {
            "user_id": [1, 2, 3],
            "age": [25, 30, 22],
            "income": [50000.0, 60000.0, 45000.0],
            "event_timestamp": pd.to_datetime(
                ["2024-01-01", "2024-01-02", "2024-01-03"]
            ),
        }
    )
    user_df.to_parquet(user_data_path)

    # Create product data
    product_df = pd.DataFrame(
        {
            "product_id": [101, 102, 103],
            "price": [29.99, 15.99, 99.99],
            "category": ["electronics", "books", "electronics"],
            "event_timestamp": pd.to_datetime(
                ["2024-01-01", "2024-01-02", "2024-01-03"]
            ),
        }
    )
    product_df.to_parquet(product_data_path)

    # Create transaction data
    transaction_df = pd.DataFrame(
        {
            "transaction_id": [1001, 1002, 1003],
            "amount": [100.0, 50.0, 200.0],
            "payment_method": ["credit", "debit", "credit"],
            "event_timestamp": pd.to_datetime(
                ["2024-01-01", "2024-01-02", "2024-01-03"]
            ),
        }
    )
    transaction_df.to_parquet(transaction_data_path)

    # Setup repo config
    config = {
        "registry": registry_path,
        "project": "test_project",
        "provider": "local",
        "offline_store": {"type": "file"},
        "online_store": {"type": "sqlite", "path": ":memory:"},
    }

    # Create data sources
    user_source = FileSource(
        name="user_source",
        path=user_data_path,
        event_timestamp_column="event_timestamp",
    )

    product_source = FileSource(
        name="product_source",
        path=product_data_path,
        event_timestamp_column="event_timestamp",
    )

    transaction_source = FileSource(
        name="transaction_source",
        path=transaction_data_path,
        event_timestamp_column="event_timestamp",
    )

    # Create feature store
    store = FeatureStore(config=RepoConfig.model_validate(config))

    # Create entities
    user_entity = Entity(
        name="user",
        value_type=ValueType.INT64,
        description="User entity for customer data",
        tags={"team": "data", "environment": "test"},
    )

    product_entity = Entity(
        name="product",
        value_type=ValueType.INT64,
        description="Product entity for catalog data",
        tags={"team": "product", "environment": "test"},
    )

    transaction_entity = Entity(
        name="transaction",
        value_type=ValueType.INT64,
        description="Transaction entity for payment data",
        tags={"team": "finance", "environment": "test"},
    )

    # Create feature views
    user_features = FeatureView(
        name="user_features",
        entities=[user_entity],
        ttl=None,
        schema=[
            Field(name="age", dtype=Int64),
            Field(name="income", dtype=Float64),
        ],
        source=user_source,
        description="User demographic features",
        tags={"team": "data", "version": "v1"},
    )

    product_features = FeatureView(
        name="product_features",
        entities=[product_entity],
        ttl=None,
        schema=[
            Field(name="price", dtype=Float64),
            Field(name="category", dtype=String),
        ],
        source=product_source,
        description="Product catalog features",
        tags={"team": "product", "version": "v2"},
    )

    transaction_features = FeatureView(
        name="transaction_features",
        entities=[transaction_entity],
        ttl=None,
        schema=[
            Field(name="amount", dtype=Float64),
            Field(name="payment_method", dtype=String),
        ],
        source=transaction_source,
        description="Transaction payment features",
        tags={"team": "finance", "version": "v1"},
    )

    # Create feature services
    user_service = FeatureService(
        name="user_service",
        features=[user_features],
        description="Service for user-related features",
        tags={"team": "data", "type": "serving"},
    )

    product_service = FeatureService(
        name="product_service",
        features=[product_features],
        description="Service for product catalog features",
        tags={"team": "product", "type": "serving"},
    )

    # Create an on-demand feature view
    request_source = RequestSource(
        name="user_request_source",
        schema=[
            Field(name="user_id", dtype=Int64),
            Field(name="conversion_rate", dtype=Float64),
        ],
    )

    @on_demand_feature_view(
        sources=[user_features, request_source],
        schema=[
            Field(name="age_conversion_score", dtype=Float64),
        ],
        description="On-demand features combining user features with real-time data",
        tags={"team": "data", "type": "real_time", "environment": "test"},
    )
    def user_on_demand_features(inputs: dict):
        # Access individual feature columns directly from inputs
        age = inputs["age"]  # from user_features feature view
        conversion_rate = inputs["conversion_rate"]  # from request source

        # Create age-based conversion score
        age_conversion_score = age * conversion_rate

        return pd.DataFrame(
            {
                "age_conversion_score": age_conversion_score,
            }
        )

    # Create saved datasets
    user_dataset_storage = SavedDatasetFileStorage(path=user_data_path)
    user_dataset = SavedDataset(
        name="user_training_dataset",
        features=["user_features:age", "user_features:income"],
        join_keys=["user"],
        storage=user_dataset_storage,
        tags={"environment": "test", "purpose": "training", "team": "data"},
    )

    # Apply all objects
    store.apply(
        [
            user_entity,
            product_entity,
            transaction_entity,
            user_features,
            product_features,
            transaction_features,
            user_service,
            product_service,
            user_on_demand_features,
        ]
    )
    store.registry.apply_saved_dataset(user_dataset, "test_project")

    global global_store
    global_store = store

    # Build REST app
    rest_server = RestRegistryServer(store)
    client = TestClient(rest_server.app)

    yield client

    tmp_dir.cleanup()


@pytest.fixture
def multi_project_search_test_app():
    """Test fixture that sets up multiple projects with overlapping resource names for comprehensive multi-project search testing"""
    # Create temp registry and data directory
    tmp_dir = tempfile.TemporaryDirectory()
    registry_path = os.path.join(tmp_dir.name, "registry.db")

    # Create dummy parquet files for different projects with proper entity columns
    data_paths = {}
    entity_data = {
        "project_a": {
            "user_id": [1, 2, 3],
            "driver_id": [11, 12, 13],
            "trip_id": [21, 22, 23],
        },
        "project_b": {
            "user_id": [4, 5, 6],
            "restaurant_id": [14, 15, 16],
            "order_id": [24, 25, 26],
        },
        "project_c": {
            "customer_id": [7, 8, 9],
            "product_id": [17, 18, 19],
            "transaction_id": [27, 28, 29],
        },
    }

    for project in ["project_a", "project_b", "project_c"]:
        data_paths[project] = os.path.join(tmp_dir.name, f"{project}_data.parquet")

        # Create comprehensive data with all entity IDs and feature columns for this project
        base_data = {
            "event_timestamp": pd.to_datetime(
                ["2024-01-01", "2024-01-02", "2024-01-03"]
            )
        }

        # Add entity columns for this project
        for entity_col, values in entity_data[project].items():
            base_data[entity_col] = values

        # Add feature columns that will be used by feature views
        feature_columns = {
            "user_features_value": [10.0, 20.0, 30.0],
            "feature_1_value": [11.0, 21.0, 31.0],
            "feature_2_value": [12.0, 22.0, 32.0],
            "driver_features_value": [13.0, 23.0, 33.0],
            "restaurant_features_value": [14.0, 24.0, 34.0],
            "customer_analytics_value": [15.0, 25.0, 35.0],
            "product_analytics_value": [16.0, 26.0, 36.0],
            "sales_features_value": [17.0, 27.0, 37.0],
        }

        for feature_col, values in feature_columns.items():
            base_data[feature_col] = values

        df = pd.DataFrame(base_data)
        df.to_parquet(data_paths[project])

    # Setup projects with overlapping resource names
    projects_data = {
        "project_a": {
            "description": "Ride sharing platform project",
            "domain": "transportation",
            "entities": [
                {"name": "user", "desc": "User entity for ride sharing"},
                {"name": "driver", "desc": "Driver entity for ride sharing"},
                {"name": "trip", "desc": "Trip entity for ride tracking"},
            ],
            "feature_views": [
                {
                    "name": "user_features",
                    "desc": "User demographic and rating features for rides",
                },
                {"name": "driver_features", "desc": "Driver performance and ratings"},
                {"name": "trip_features", "desc": "Trip duration and cost features"},
            ],
            "feature_services": [
                {
                    "name": "user_service",
                    "desc": "Service for user features in ride sharing",
                },
                {"name": "driver_service", "desc": "Service for driver matching"},
            ],
            "data_sources": [
                {"name": "user_data", "desc": "User data source for ride sharing"},
                {"name": "common_analytics", "desc": "Common analytics data source"},
            ],
        },
        "project_b": {
            "description": "Food delivery platform project",
            "domain": "food_delivery",
            "entities": [
                {
                    "name": "user",
                    "desc": "User entity for food delivery",
                },  # Same name as project_a
                {"name": "restaurant", "desc": "Restaurant entity for food delivery"},
                {"name": "order", "desc": "Order entity for food tracking"},
            ],
            "feature_views": [
                {
                    "name": "user_features",
                    "desc": "User preferences and order history for food",
                },  # Same name as project_a
                {
                    "name": "restaurant_features",
                    "desc": "Restaurant ratings and cuisine types",
                },
                {
                    "name": "order_features",
                    "desc": "Order value and delivery time features",
                },
            ],
            "feature_services": [
                {
                    "name": "user_service",
                    "desc": "Service for user features in food delivery",
                },  # Same name as project_a
                {
                    "name": "recommendation_service",
                    "desc": "Service for restaurant recommendations",
                },
            ],
            "data_sources": [
                {
                    "name": "restaurant_data",
                    "desc": "Restaurant data source for food delivery",
                },
                {
                    "name": "common_analytics",
                    "desc": "Common analytics data source",
                },  # Same name as project_a
            ],
        },
        "project_c": {
            "description": "E-commerce analytics project",
            "domain": "ecommerce",
            "entities": [
                {"name": "customer", "desc": "Customer entity for e-commerce"},
                {"name": "product", "desc": "Product entity for catalog"},
                {"name": "transaction", "desc": "Transaction entity for purchases"},
            ],
            "feature_views": [
                {"name": "customer_analytics", "desc": "Customer behavior analytics"},
                {"name": "product_analytics", "desc": "Product performance metrics"},
                {"name": "sales_features", "desc": "Sales and revenue features"},
            ],
            "feature_services": [
                {"name": "analytics_service", "desc": "Service for customer analytics"},
                {
                    "name": "product_service",
                    "desc": "Service for product recommendations",
                },
            ],
            "data_sources": [
                {"name": "sales_data", "desc": "Sales transaction data"},
                {"name": "inventory_data", "desc": "Product inventory data"},
            ],
        },
    }

    # Create a single registry to hold all projects
    base_config = {
        "registry": registry_path,
        "provider": "local",
        "offline_store": {"type": "file"},
        "online_store": {"type": "sqlite", "path": ":memory:"},
    }

    # Create a master FeatureStore instance for managing the shared registry
    master_config = {**base_config, "project": "project_a"}  # Use project_a as base
    master_store = FeatureStore(config=RepoConfig.model_validate(master_config))

    # First, create the Project objects in the registry

    for project_name, project_data in projects_data.items():
        project_obj = Project(
            name=project_name,
            description=project_data["description"],
            tags={"domain": project_data["domain"]},
        )
        master_store.registry.apply_project(project_obj)

    # Create resources for each project and apply them to the shared registry
    for project_name, project_data in projects_data.items():
        # Create data sources for this project
        data_sources = []
        for ds in project_data["data_sources"]:
            # Make data source names unique across projects to avoid conflicts
            unique_name = (
                f"{project_name}_{ds['name']}"
                if ds["name"] == "common_analytics"
                else ds["name"]
            )

            source = FileSource(
                name=unique_name,
                path=data_paths[project_name],
                event_timestamp_column="event_timestamp",
            )
            # Ensure the data source has the correct project set
            if hasattr(source, "project"):
                source.project = project_name
            data_sources.append(source)

        # Create entities for this project with proper join keys
        entities = []
        entity_mapping = {
            "project_a": {"user": "user_id", "driver": "driver_id", "trip": "trip_id"},
            "project_b": {
                "user": "user_id",
                "restaurant": "restaurant_id",
                "order": "order_id",
            },
            "project_c": {
                "customer": "customer_id",
                "product": "product_id",
                "transaction": "transaction_id",
            },
        }

        for ent in project_data["entities"]:
            join_key = entity_mapping[project_name][ent["name"]]
            entity = Entity(
                name=ent["name"],
                join_keys=[join_key],
                value_type=ValueType.INT64,  # Add required value_type
                description=ent["desc"],
                tags={
                    "project": project_name,
                    "domain": project_data["domain"],
                    "environment": "test",
                },
            )
            # Ensure the entity has the correct project set
            entity.project = project_name
            entities.append(entity)

        # Create feature views for this project with proper entity relationships
        feature_views = []

        # Map feature view names to their corresponding feature columns
        feature_column_mapping = {
            "user_features": "user_features_value",
            "driver_features": "driver_features_value",
            "trip_features": "feature_1_value",
            "restaurant_features": "restaurant_features_value",
            "order_features": "feature_2_value",
            "customer_analytics": "customer_analytics_value",
            "product_analytics": "product_analytics_value",
            "sales_features": "sales_features_value",
        }

        for i, fv in enumerate(project_data["feature_views"]):
            # Alternate between data sources and entities
            source = data_sources[i % len(data_sources)]
            entity = entities[i % len(entities)]  # Use different entities

            # Get the correct feature column name for this feature view
            feature_column = feature_column_mapping.get(
                fv["name"], f"feature_{i}_value"
            )

            # Get the entity's join key for the schema
            entity_join_key = entity.join_key

            feature_view = FeatureView(
                name=fv["name"],
                entities=[entity],
                ttl=None,
                schema=[
                    # Include entity column in schema
                    Field(name=entity_join_key, dtype=Int64),
                    # Include feature column in schema
                    Field(name=feature_column, dtype=Float64),
                ],
                source=source,
                description=fv["desc"],
                tags={
                    "project": project_name,
                    "domain": project_data["domain"],
                    "team": f"team_{project_name}",
                    "version": f"v{i + 1}",
                },
            )
            # Ensure the feature view has the correct project set
            feature_view.project = project_name
            feature_views.append(feature_view)

        # Create feature services for this project
        feature_services = []
        for i, fs in enumerate(project_data["feature_services"]):
            # Use different feature views for each service
            fv_subset = (
                feature_views[i : i + 2]
                if i + 1 < len(feature_views)
                else [feature_views[i]]
            )

            service = FeatureService(
                name=fs["name"],
                features=fv_subset,
                description=fs["desc"],
                tags={
                    "project": project_name,
                    "domain": project_data["domain"],
                    "service_type": "real_time",
                },
            )
            # Ensure the feature service has the correct project set
            service.project = project_name
            feature_services.append(service)

        # Apply all objects for this project directly to the registry
        for entity in entities:
            master_store.registry.apply_entity(entity, project_name)

        for data_source in data_sources:
            master_store.registry.apply_data_source(data_source, project_name)

        for feature_view in feature_views:
            master_store.registry.apply_feature_view(feature_view, project_name)

        for feature_service in feature_services:
            master_store.registry.apply_feature_service(feature_service, project_name)

    # Ensure registry is committed
    master_store.registry.commit()

    # Build REST app using the master store's registry (contains all projects)
    rest_server = RestRegistryServer(master_store)
    client = TestClient(rest_server.app)

    yield client

    tmp_dir.cleanup()


@pytest.fixture
def shared_search_responses(search_test_app):
    """Pre-computed responses for common search scenarios to reduce API calls"""
    return {
        "user_query": search_test_app.get("/search?query=user").json(),
        "empty_query": search_test_app.get("/search?query=").json(),
        "nonexistent_query": search_test_app.get("/search?query=xyz_12345").json(),
        "paginated_basic": search_test_app.get("/search?query=&page=1&limit=5").json(),
        "paginated_page2": search_test_app.get("/search?query=&page=2&limit=3").json(),
        "sorted_by_name": search_test_app.get(
            "/search?query=&sort_by=name&sort_order=asc"
        ).json(),
        "sorted_by_match_score": search_test_app.get(
            "/search?query=user&sort_by=match_score&sort_order=desc"
        ).json(),
        "with_tags": search_test_app.get("/search?query=&tags=team:data").json(),
        "feature_name_query": search_test_app.get("/search?query=age").json(),
    }
