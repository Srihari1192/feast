import logging
import os
import uuid
from typing import Any, Callable, List, Optional, Union

import pandas as pd
import pyarrow as pa
from ray.data import Dataset

from feast.dataframe import DataFrameEngine, FeastDataFrame
from feast.errors import SavedDatasetLocationAlreadyExists
from feast.infra.offline_stores.offline_store import RetrievalJob, RetrievalMetadata
from feast.infra.ray_shared_utils import is_ray_data
from feast.infra.ray_initializer import get_ray_wrapper
from feast.on_demand_feature_view import OnDemandFeatureView
from feast.saved_dataset import SavedDatasetStorage, ValidationReference

from feast.infra.offline_stores.file_source import SavedDatasetFileStorage

from .config import RayOfflineStoreConfig
from .utils import _safe_infer_event_timestamp_column, _safe_get_entity_timestamp_bounds

logger = logging.getLogger(__name__)

# Remote storage URI schemes supported by the Ray offline store
REMOTE_STORAGE_SCHEMES = ("s3://", "gs://", "hdfs://", "abfs://", "abfss://")


class RayRetrievalJob(RetrievalJob):
    def __init__(
        self,
        dataset_or_callable: Union[
            Dataset, pd.DataFrame, Callable[[], Union[Dataset, pd.DataFrame]]
        ],
        staging_location: Optional[str] = None,
        config: Optional[RayOfflineStoreConfig] = None,
    ):
        self._dataset_or_callable = dataset_or_callable
        self._staging_location = staging_location
        self._config = config or RayOfflineStoreConfig()
        self._cached_df: Optional[pd.DataFrame] = None
        self._cached_dataset: Optional[Dataset] = None
        self._metadata: Optional[RetrievalMetadata] = None
        self._full_feature_names: bool = False
        self._on_demand_feature_views: Optional[List[OnDemandFeatureView]] = None
        self._feature_refs: List[str] = []
        self._entity_df: Optional[pd.DataFrame] = None
        self._prefer_ray_datasets: bool = True

    def _create_metadata(self) -> RetrievalMetadata:
        """Create metadata from the entity DataFrame and feature references."""
        if self._entity_df is not None:
            timestamp_col = _safe_infer_event_timestamp_column(
                self._entity_df, "event_timestamp"
            )
            min_timestamp, max_timestamp = _safe_get_entity_timestamp_bounds(
                self._entity_df, timestamp_col
            )

            keys = [col for col in self._entity_df.columns if col != timestamp_col]
        else:
            try:
                result = self._resolve()
                if is_ray_data(result):
                    timestamp_col = _safe_infer_event_timestamp_column(
                        result, "event_timestamp"
                    )
                    min_timestamp, max_timestamp = _safe_get_entity_timestamp_bounds(
                        result, timestamp_col
                    )
                    schema = result.schema()
                    keys = [col for col in schema.names if col != timestamp_col]
                else:
                    min_timestamp = None
                    max_timestamp = None
                    keys = []
            except Exception:
                min_timestamp = None
                max_timestamp = None
                keys = []

        return RetrievalMetadata(
            features=self._feature_refs,
            keys=keys,
            min_event_timestamp=min_timestamp,
            max_event_timestamp=max_timestamp,
        )

    def _set_metadata_info(
        self, feature_refs: List[str], entity_df: pd.DataFrame
    ) -> None:
        """Set the feature references and entity DataFrame for metadata creation."""
        self._feature_refs = feature_refs
        self._entity_df = entity_df

    def _resolve(self) -> Union[Dataset, pd.DataFrame]:
        if callable(self._dataset_or_callable):
            result = self._dataset_or_callable()
        else:
            result = self._dataset_or_callable
        return result

    def _get_ray_dataset(self) -> Dataset:
        """Get the result as a Ray Dataset, converting if necessary."""
        if self._cached_dataset is not None:
            return self._cached_dataset

        result = self._resolve()
        if is_ray_data(result):
            self._cached_dataset = result
            return result
        elif isinstance(result, pd.DataFrame):
            ray_wrapper = get_ray_wrapper()
            self._cached_dataset = ray_wrapper.from_pandas(result)
            return self._cached_dataset
        else:
            raise ValueError(f"Unsupported result type: {type(result)}")

    def to_df(
        self,
        validation_reference: Optional[ValidationReference] = None,
        timeout: Optional[int] = None,
    ) -> pd.DataFrame:
        if self._cached_df is not None and not self.on_demand_feature_views:
            df = self._cached_df
        else:
            if self.on_demand_feature_views:
                df = super().to_df(
                    validation_reference=validation_reference, timeout=timeout
                )
            else:
                if self._prefer_ray_datasets:
                    ray_ds = self._get_ray_dataset()
                    df = ray_ds.to_pandas()
                else:
                    result = self._resolve()
                    if isinstance(result, pd.DataFrame):
                        df = result
                    else:
                        df = result.to_pandas()
                self._cached_df = df

        if validation_reference:
            try:
                from feast.dqm.errors import ValidationFailed

                validation_result = validation_reference.profile.validate(df)
                if not validation_result.is_success:
                    raise ValidationFailed(validation_result)
            except ImportError:
                logger.warning("DQM profiler not available, skipping validation")
            except Exception as e:
                logger.error(f"Validation failed: {e}")
                raise ValueError(f"Data validation failed: {e}")
        return df

    def to_arrow(
        self,
        validation_reference: Optional[ValidationReference] = None,
        timeout: Optional[int] = None,
    ) -> pa.Table:
        if self.on_demand_feature_views:
            return super().to_arrow(
                validation_reference=validation_reference, timeout=timeout
            )

        if self._prefer_ray_datasets:
            try:
                ray_ds = self._get_ray_dataset()
                if hasattr(ray_ds, "to_arrow"):
                    return ray_ds.to_arrow()
                else:
                    df = ray_ds.to_pandas()
                    return pa.Table.from_pandas(df)
            except Exception:
                df = self.to_df(
                    validation_reference=validation_reference, timeout=timeout
                )
                return pa.Table.from_pandas(df)
        else:
            result = self._resolve()
            if isinstance(result, pd.DataFrame):
                return pa.Table.from_pandas(result)
            else:
                df = result.to_pandas()
                return pa.Table.from_pandas(df)

    def to_feast_df(
        self,
        validation_reference: Optional[ValidationReference] = None,
        timeout: Optional[int] = None,
    ) -> FeastDataFrame:
        """
        Return the result as a FeastDataFrame with Ray engine.

        This preserves Ray's lazy execution by wrapping the Ray Dataset directly.
        """
        # If we have on-demand feature views, fall back to base class Arrow implementation
        if self.on_demand_feature_views:
            return super().to_feast_df(validation_reference, timeout)

        # Get the Ray Dataset directly (maintains lazy execution)
        ray_ds = self._get_ray_dataset()

        return FeastDataFrame(
            data=ray_ds,
            engine=DataFrameEngine.RAY,
        )

    def to_remote_storage(self) -> list[str]:
        if not self._staging_location:
            raise ValueError("Staging location must be set for remote materialization.")
        try:
            ray_ds = self._get_ray_dataset()
            # Import here to avoid circular imports
            from .offline_store import RayOfflineStore
            RayOfflineStore._ensure_ray_initialized()
            output_uri = os.path.join(self._staging_location, str(uuid.uuid4()))
            ray_ds.write_parquet(output_uri)
            return [output_uri]
        except Exception as e:
            raise RuntimeError(f"Failed to write to remote storage: {e}")

    @property
    def metadata(self) -> Optional[RetrievalMetadata]:
        """Return metadata information about retrieval."""
        if self._metadata is None:
            self._metadata = self._create_metadata()
        return self._metadata

    @property
    def full_feature_names(self) -> bool:
        return self._full_feature_names

    @property
    def on_demand_feature_views(self) -> List[OnDemandFeatureView]:
        return self._on_demand_feature_views or []

    def to_sql(self) -> str:
        raise NotImplementedError("SQL export not supported for Ray offline store")

    def _to_df_internal(self, timeout: Optional[int] = None) -> pd.DataFrame:
        if self._prefer_ray_datasets:
            ray_ds = self._get_ray_dataset()
            return ray_ds.to_pandas()
        else:
            return self._resolve().to_pandas()

    def _to_arrow_internal(self, timeout: Optional[int] = None) -> pa.Table:
        if self._prefer_ray_datasets:
            ray_ds = self._get_ray_dataset()
            try:
                if hasattr(ray_ds, "to_arrow"):
                    return ray_ds.to_arrow()
                else:
                    df = ray_ds.to_pandas()
                    return pa.Table.from_pandas(df)
            except Exception:
                df = ray_ds.to_pandas()
                return pa.Table.from_pandas(df)
        else:
            result = self._resolve()
            if isinstance(result, pd.DataFrame):
                return pa.Table.from_pandas(result)
            else:
                df = result.to_pandas()
                return pa.Table.from_pandas(df)

    def persist(
        self,
        storage: SavedDatasetStorage,
        allow_overwrite: Optional[bool] = False,
        timeout: Optional[int] = None,
    ) -> str:
        """Persist the dataset to storage using Ray operations."""

        if not isinstance(storage, SavedDatasetFileStorage):
            raise ValueError(
                f"Ray offline store only supports SavedDatasetFileStorage, got {type(storage)}"
            )
        destination_path = storage.file_options.uri
        if not destination_path.startswith(REMOTE_STORAGE_SCHEMES):
            if not allow_overwrite and os.path.exists(destination_path):
                raise SavedDatasetLocationAlreadyExists(location=destination_path)
        try:
            ray_ds = self._get_ray_dataset()

            if not destination_path.startswith(REMOTE_STORAGE_SCHEMES):
                os.makedirs(os.path.dirname(destination_path), exist_ok=True)

            ray_ds.write_parquet(destination_path)

            return destination_path
        except Exception as e:
            raise RuntimeError(f"Failed to persist dataset to {destination_path}: {e}")

    def materialize(self) -> None:
        """Materialize the Ray dataset to improve subsequent access performance."""
        try:
            ray_ds = self._get_ray_dataset()
            materialized_ds = ray_ds.materialize()
            self._cached_dataset = materialized_ds

            if getattr(self._config, "enable_ray_logging", False):
                logger.info("Ray dataset materialized successfully")
        except Exception as e:
            logger.warning(f"Failed to materialize Ray dataset: {e}")

    def schema(self) -> pa.Schema:
        """Get the schema of the dataset efficiently using Ray operations."""
        try:
            ray_ds = self._get_ray_dataset()
            return ray_ds.schema()
        except Exception:
            df = self.to_df()
            return pa.Table.from_pandas(df).schema
