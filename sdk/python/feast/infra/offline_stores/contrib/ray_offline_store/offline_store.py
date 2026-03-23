import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

import dill
import fsspec
import numpy as np
import pandas as pd
import pyarrow as pa
from ray.data import Dataset

from feast.data_source import DataSource
from feast.errors import RequestDataNotFoundInEntityDfException
from feast.feature_logging import LoggingConfig, LoggingSource
from feast.feature_view import DUMMY_ENTITY_ID, DUMMY_ENTITY_VAL, FeatureView
from feast.feature_view_utils import resolve_feature_view_source_with_fallback
from feast.infra.offline_stores.file_source import (
    FileLoggingDestination,
    FileSource,
    SavedDatasetFileStorage,
)
from feast.infra.offline_stores.offline_store import OfflineStore, RetrievalJob
from feast.infra.ray_initializer import ensure_ray_initialized, get_ray_wrapper
from feast.infra.ray_shared_utils import (
    _build_required_columns,
    apply_field_mapping,
    ensure_timestamp_compatibility,
    normalize_timestamp_columns,
)
from feast.infra.registry.base_registry import BaseRegistry
from feast.on_demand_feature_view import OnDemandFeatureView
from feast.repo_config import RepoConfig
from feast.saved_dataset import SavedDatasetStorage
from feast.utils import _get_column_names, make_df_tzaware, make_tzaware

from .config import RayOfflineStoreConfig, RayResourceManager
from .processor import RayDataProcessor
from .retrieval_job import RayRetrievalJob, REMOTE_STORAGE_SCHEMES
from .utils import (
    _convert_feature_column_types,
    _handle_empty_dataframe_case,
    _safe_validate_schema,
)

logger = logging.getLogger(__name__)


def _compute_non_entity_dates_ray(
    feature_views: List[FeatureView],
    start_date_opt: Optional[datetime],
    end_date_opt: Optional[datetime],
) -> Tuple[datetime, datetime]:
    # Why: derive bounded time window when no entity_df is provided using explicit dates or max TTL fallback
    end_date = (
        make_tzaware(end_date_opt) if end_date_opt else make_tzaware(datetime.utcnow())
    )
    if start_date_opt is None:
        max_ttl_seconds = 0
        for fv in feature_views:
            if getattr(fv, "ttl", None):
                try:
                    ttl_val = fv.ttl
                    if isinstance(ttl_val, timedelta):
                        max_ttl_seconds = max(
                            max_ttl_seconds, int(ttl_val.total_seconds())
                        )
                except Exception:
                    pass
        start_date = (
            end_date - timedelta(seconds=max_ttl_seconds)
            if max_ttl_seconds > 0
            else end_date - timedelta(days=30)
        )
    else:
        start_date = make_tzaware(start_date_opt)
    return start_date, end_date


def _make_filter_range(timestamp_field: str, start_date: datetime, end_date: datetime):
    # Why: factory function for time-range filtering in Ray map_batches
    def _filter_range(batch: pd.DataFrame) -> pd.Series:
        ts = pd.to_datetime(batch[timestamp_field], utc=True)
        return (ts >= start_date) & (ts <= end_date)

    return _filter_range


def _make_select_distinct_entity_timestamps(join_keys: List[str], timestamp_field: str):
    # Why: factory function for distinct (entity_keys, event_timestamp) projection in Ray map_batches
    # This preserves multiple transactions per entity ID with different timestamps for proper PIT joins
    def _select_distinct_entity_timestamps(batch: pd.DataFrame) -> pd.DataFrame:
        cols = [c for c in join_keys if c in batch.columns]
        if timestamp_field in batch.columns:
            # Rename timestamp to standardized event_timestamp
            batch = batch.copy()
            if timestamp_field != "event_timestamp":
                batch["event_timestamp"] = batch[timestamp_field]
            cols = cols + ["event_timestamp"]
        if not cols:
            return pd.DataFrame(columns=join_keys + ["event_timestamp"])
        return batch[cols].drop_duplicates().reset_index(drop=True)

    return _select_distinct_entity_timestamps


def _distinct_entities_for_feature_view_ray(
    store: "RayOfflineStore",
    config: RepoConfig,
    fv: FeatureView,
    registry: BaseRegistry,
    project: str,
    start_date: datetime,
    end_date: datetime,
) -> Tuple[Dataset, List[str]]:
    # Why: read minimal columns, filter by time, and project distinct (join_keys, event_timestamp) per FeatureView
    # This preserves multiple transactions per entity ID for proper point-in-time joins
    ray_wrapper = get_ray_wrapper()
    entities = fv.entities or []
    entity_objs = [registry.get_entity(e, project) for e in entities]
    original_join_keys, _rev_feats, timestamp_field, _created_col = _get_column_names(
        fv, entity_objs
    )

    source_info = resolve_feature_view_source_with_fallback(
        fv, config, is_materialization=False
    )
    source_path = store._get_source_path(source_info.data_source, config)
    required_columns = list(set(original_join_keys + [timestamp_field]))
    ds = ray_wrapper.read_parquet(source_path, columns=required_columns)

    field_mapping = getattr(fv.batch_source, "field_mapping", None)
    if field_mapping:
        ds = apply_field_mapping(ds, field_mapping)
        original_join_keys = [field_mapping.get(k, k) for k in original_join_keys]
        timestamp_field = field_mapping.get(timestamp_field, timestamp_field)

    if fv.projection.join_key_map:
        join_keys = [
            fv.projection.join_key_map.get(key, key) for key in original_join_keys
        ]
    else:
        join_keys = original_join_keys

    ds = ensure_timestamp_compatibility(ds, [timestamp_field])
    ds = ds.filter(_make_filter_range(timestamp_field, start_date, end_date))
    # Extract distinct (entity_keys, event_timestamp) combinations - not just entity_keys
    ds = ds.map_batches(
        _make_select_distinct_entity_timestamps(join_keys, timestamp_field),
        batch_format="pandas",
    )
    return ds, join_keys


def _make_align_columns(all_join_keys: List[str], include_timestamp: bool = False):
    # Why: factory function for schema alignment in Ray map_batches
    # When include_timestamp=True, also aligns event_timestamp column for proper PIT joins
    def _align_columns(batch: pd.DataFrame) -> pd.DataFrame:
        batch = batch.copy()
        output_cols = list(all_join_keys)
        if include_timestamp:
            output_cols = output_cols + ["event_timestamp"]
        for k in output_cols:
            if k not in batch.columns:
                batch[k] = pd.NA
        return batch[output_cols]

    return _align_columns


def _make_distinct_by_keys(keys: List[str], include_timestamp: bool = False):
    # Why: factory function for deduplication in Ray map_batches
    # When include_timestamp=True, deduplicates on (keys + event_timestamp) for proper PIT joins
    def _distinct(batch: pd.DataFrame) -> pd.DataFrame:
        subset = list(keys)
        if include_timestamp and "event_timestamp" in batch.columns:
            subset = subset + ["event_timestamp"]
        return batch.drop_duplicates(subset=subset).reset_index(drop=True)

    return _distinct


def _align_and_union_entities_ray(
    datasets: List[Dataset],
    all_join_keys: List[str],
    include_timestamp: bool = False,
) -> Dataset:
    # Why: align schemas across FeatureViews and union to a unified entity set
    # When include_timestamp=True, preserves distinct (entity_keys, event_timestamp) combinations
    # for proper point-in-time joins with multiple transactions per entity
    ray_wrapper = get_ray_wrapper()
    output_cols = list(all_join_keys)
    if include_timestamp:
        output_cols = output_cols + ["event_timestamp"]
    if not datasets:
        return ray_wrapper.from_pandas(pd.DataFrame(columns=output_cols))

    aligned = [
        ds.map_batches(
            _make_align_columns(all_join_keys, include_timestamp=include_timestamp),
            batch_format="pandas",
        )
        for ds in datasets
    ]
    entity_ds = aligned[0]
    for ds in aligned[1:]:
        entity_ds = entity_ds.union(ds)
    return entity_ds.map_batches(
        _make_distinct_by_keys(all_join_keys, include_timestamp=include_timestamp),
        batch_format="pandas",
    )


class RayOfflineStore(OfflineStore):
    def __init__(self) -> None:
        self._staging_location: Optional[str] = None
        self._ray_initialized: bool = False
        self._resource_manager: Optional[RayResourceManager] = None
        self._data_processor: Optional[RayDataProcessor] = None

    @staticmethod
    def _suppress_ray_logging() -> None:
        """Suppress Ray and Ray Data logging completely."""
        import warnings

        # Suppress Ray warnings
        warnings.filterwarnings("ignore", category=DeprecationWarning, module="ray")
        warnings.filterwarnings("ignore", category=UserWarning, module="ray")

        # Set environment variables to suppress Ray output
        os.environ["RAY_DISABLE_IMPORT_WARNING"] = "1"
        os.environ["RAY_SUPPRESS_UNVERIFIED_TLS_WARNING"] = "1"
        os.environ["RAY_LOG_LEVEL"] = "ERROR"
        os.environ["RAY_DATA_LOG_LEVEL"] = "ERROR"
        os.environ["RAY_DISABLE_PROGRESS_BARS"] = "1"

        # Suppress all Ray-related loggers
        ray_loggers = [
            "ray",
            "ray.data",
            "ray.data.dataset",
            "ray.data.context",
            "ray.data._internal.streaming_executor",
            "ray.data._internal.execution",
            "ray.data._internal",
            "ray.tune",
            "ray.serve",
            "ray.util",
            "ray._private",
        ]
        for logger_name in ray_loggers:
            logging.getLogger(logger_name).setLevel(logging.ERROR)

        # Configure DatasetContext to disable progress bars
        try:
            from ray.data.context import DatasetContext

            ctx = DatasetContext.get_current()
            ctx.enable_progress_bars = False
            if hasattr(ctx, "verbose_progress"):
                ctx.verbose_progress = False
        except Exception:
            pass  # Ignore if Ray Data is not available

    @staticmethod
    def _ensure_ray_initialized(config: Optional[RepoConfig] = None) -> None:
        """Ensure Ray is initialized with proper configuration."""
        ensure_ray_initialized(config)

    def _init_ray(self, config: RepoConfig) -> None:
        ray_config = config.offline_store
        assert isinstance(ray_config, RayOfflineStoreConfig)

        RayOfflineStore._ensure_ray_initialized(config)

        if self._resource_manager is None:
            self._resource_manager = RayResourceManager(ray_config)
            self._resource_manager.configure_ray_context()
        if self._data_processor is None:
            self._data_processor = RayDataProcessor(self._resource_manager)

    def _get_source_path(self, source: DataSource, config: RepoConfig) -> str:
        if not isinstance(source, FileSource):
            raise ValueError("RayOfflineStore currently only supports FileSource")
        repo_path = getattr(config, "repo_path", None)
        uri = FileSource.get_uri_for_file_path(repo_path, source.path)
        return uri

    def _optimize_dataset_for_operation(self, ds: Dataset, operation: str) -> Dataset:
        """Optimize dataset for specific operations."""
        if self._resource_manager is None:
            return ds

        dataset_size = ds.size_bytes()
        requirements = self._resource_manager.estimate_processing_requirements(
            dataset_size, operation
        )

        if requirements["can_fit_in_memory"]:
            ds = ds.materialize()

        optimal_partitions = requirements["optimal_partitions"]
        current_partitions = ds.num_blocks()

        if current_partitions != optimal_partitions:
            if getattr(self._resource_manager.config, "enable_ray_logging", False):
                logger.debug(
                    f"Repartitioning dataset from {current_partitions} to {optimal_partitions} blocks"
                )
            ds = ds.repartition(num_blocks=optimal_partitions)

        return ds

    @staticmethod
    def offline_write_batch(
        config: RepoConfig,
        feature_view: FeatureView,
        table: pa.Table,
        progress: Optional[Callable[[int], Any]] = None,
    ) -> None:
        """Write batch data using Ray operations with performance monitoring."""
        import time

        start_time = time.time()

        RayOfflineStore._ensure_ray_initialized(config)

        repo_path = getattr(config, "repo_path", None) or os.getcwd()
        ray_config = config.offline_store
        assert isinstance(ray_config, RayOfflineStoreConfig)

        if not ray_config.enable_ray_logging:
            RayOfflineStore._suppress_ray_logging()
        assert isinstance(feature_view.batch_source, FileSource)

        validation_result = _safe_validate_schema(
            config, feature_view.batch_source, table.column_names, "offline_write_batch"
        )

        if validation_result:
            expected_schema, expected_columns = validation_result
            if expected_columns != table.column_names and set(expected_columns) == set(
                table.column_names
            ):
                if getattr(ray_config, "enable_ray_logging", False):
                    logger.info("Reordering table columns to match expected schema")
                table = table.select(expected_columns)

        batch_source_path = feature_view.batch_source.file_options.uri
        feature_path = FileSource.get_uri_for_file_path(repo_path, batch_source_path)

        ray_wrapper = get_ray_wrapper()
        ds = ray_wrapper.from_arrow(table)

        try:
            if feature_path.endswith(".parquet"):
                if os.path.exists(feature_path):
                    existing_ds = ray_wrapper.read_parquet(feature_path)
                    combined_ds = existing_ds.union(ds)
                    combined_ds.write_parquet(feature_path)
                else:
                    ds.write_parquet(feature_path)
            else:
                os.makedirs(feature_path, exist_ok=True)
                ds.write_parquet(feature_path)

            if progress:
                progress(table.num_rows)

        except Exception:
            if getattr(ray_config, "enable_ray_logging", False):
                logger.info("Falling back to pandas-based writing")
            df = table.to_pandas()
            if feature_path.endswith(".parquet"):
                if os.path.exists(feature_path):
                    existing_df = pd.read_parquet(feature_path)
                    combined_df = pd.concat([existing_df, df], ignore_index=True)
                    combined_df.to_parquet(feature_path, index=False)
                else:
                    df.to_parquet(feature_path, index=False)
            else:
                os.makedirs(feature_path, exist_ok=True)
                ds_fallback = ray_wrapper.from_pandas(df)
                ds_fallback.write_parquet(feature_path)

            if progress:
                progress(table.num_rows)

        duration = time.time() - start_time
        if getattr(ray_config, "enable_ray_logging", False):
            logger.info(
                f"Ray offline_write_batch performance: {table.num_rows} rows in {duration:.2f}s "
                f"({table.num_rows / duration:.0f} rows/s)"
            )

    def online_write_batch(
        self,
        config: RepoConfig,
        table: pa.Table,
        progress: Optional[Callable[[int], Any]] = None,
    ) -> None:
        """Ray offline store doesn't support online writes."""
        raise NotImplementedError("Ray offline store doesn't support online writes")

    @staticmethod
    def _process_filtered_batch(
        batch: pd.DataFrame,
        join_key_columns: List[str],
        feature_name_columns: List[str],
        timestamp_columns: List[str],
        timestamp_field_mapped: str,
    ) -> pd.DataFrame:
        batch = make_df_tzaware(batch)
        if batch.empty:
            return _handle_empty_dataframe_case(
                join_key_columns, feature_name_columns, timestamp_columns
            )

        if not join_key_columns:
            batch[DUMMY_ENTITY_ID] = DUMMY_ENTITY_VAL

        # If feature_name_columns is empty, it means "keep all columns" (for transformations)
        # Otherwise, filter to only the requested columns
        if feature_name_columns:
            all_required_columns = _build_required_columns(
                join_key_columns, feature_name_columns, timestamp_columns
            )
            available_columns = [
                col for col in all_required_columns if col in batch.columns
            ]
            batch = batch[available_columns]

        if (
            "event_timestamp" not in batch.columns
            and timestamp_field_mapped != "event_timestamp"
        ):
            if timestamp_field_mapped in batch.columns:
                batch["event_timestamp"] = batch[timestamp_field_mapped]
        return batch

    @staticmethod
    def _load_and_filter_dataset(
        source_path: str,
        data_source: DataSource,
        join_key_columns: List[str],
        feature_name_columns: List[str],
        timestamp_field: str,
        created_timestamp_column: Optional[str],
        start_date: Optional[datetime],
        end_date: Optional[datetime],
    ) -> pd.DataFrame:
        try:
            field_mapping = getattr(data_source, "field_mapping", None)

            if not feature_name_columns:
                columns_to_read = None
            else:
                columns_to_read = list(
                    set(join_key_columns + feature_name_columns + [timestamp_field])
                )
                if created_timestamp_column:
                    columns_to_read.append(created_timestamp_column)

            ds = RayOfflineStore._create_filtered_dataset(
                source_path,
                timestamp_field,
                start_date,
                end_date,
                columns=columns_to_read,
            )
            df = ds.to_pandas()
            if field_mapping:
                df = df.rename(columns=field_mapping)
            timestamp_field_mapped = (
                field_mapping.get(timestamp_field, timestamp_field)
                if field_mapping
                else timestamp_field
            )
            created_timestamp_column_mapped = (
                field_mapping.get(created_timestamp_column, created_timestamp_column)
                if field_mapping and created_timestamp_column
                else created_timestamp_column
            )
            timestamp_columns = [timestamp_field_mapped]
            if created_timestamp_column_mapped:
                timestamp_columns.append(created_timestamp_column_mapped)
            df = normalize_timestamp_columns(df, timestamp_columns, inplace=True)
            df = RayOfflineStore._process_filtered_batch(
                df,
                join_key_columns,
                feature_name_columns,
                timestamp_columns,
                timestamp_field_mapped,
            )
            existing_timestamp_columns = [
                col for col in timestamp_columns if col in df.columns
            ]
            if existing_timestamp_columns:
                df = df.sort_values(existing_timestamp_columns, ascending=False)
            df = df.reset_index(drop=True)
            return df
        except Exception as e:
            raise RuntimeError(f"Failed to load data from {source_path}: {e}")

    @staticmethod
    def _load_and_filter_dataset_ray(
        source_path: str,
        data_source: DataSource,
        join_key_columns: List[str],
        feature_name_columns: List[str],
        timestamp_field: str,
        created_timestamp_column: Optional[str],
        start_date: Optional[datetime],
        end_date: Optional[datetime],
    ) -> Dataset:
        try:
            field_mapping = getattr(data_source, "field_mapping", None)

            if not feature_name_columns:
                columns_to_read = None
            else:
                columns_to_read = list(
                    set(join_key_columns + feature_name_columns + [timestamp_field])
                )
                if created_timestamp_column:
                    columns_to_read.append(created_timestamp_column)

            ds = RayOfflineStore._create_filtered_dataset(
                source_path,
                timestamp_field,
                start_date,
                end_date,
                columns=columns_to_read,
            )
            if field_mapping:
                ds = apply_field_mapping(ds, field_mapping)
            timestamp_field_mapped = (
                field_mapping.get(timestamp_field, timestamp_field)
                if field_mapping
                else timestamp_field
            )
            created_timestamp_column_mapped = (
                field_mapping.get(created_timestamp_column, created_timestamp_column)
                if field_mapping and created_timestamp_column
                else created_timestamp_column
            )
            timestamp_columns = [timestamp_field_mapped]
            if created_timestamp_column_mapped:
                timestamp_columns.append(created_timestamp_column_mapped)
            # Exclude __log_timestamp from normalization as it's used for time range filtering
            exclude_columns = (
                ["__log_timestamp"] if "__log_timestamp" in timestamp_columns else []
            )
            ds = normalize_timestamp_columns(
                ds, timestamp_columns, exclude_columns=exclude_columns
            )
            ds = ds.map_batches(
                lambda batch: RayOfflineStore._process_filtered_batch(
                    batch,
                    join_key_columns,
                    feature_name_columns,
                    timestamp_columns,
                    timestamp_field_mapped,
                ),
                batch_format="pandas",
            )
            timestamp_columns_existing = [
                col for col in timestamp_columns if col in ds.schema().names
            ]
            if timestamp_columns_existing:
                ds = ds.sort(timestamp_columns_existing, descending=True)

            return ds
        except Exception as e:
            raise RuntimeError(f"Failed to load data from {source_path}: {e}")

    @staticmethod
    def _pull_latest_processing_ray(
        ds: Dataset,
        join_key_columns: List[str],
        timestamp_field: str,
        created_timestamp_column: Optional[str],
        field_mapping: Optional[Dict[str, str]] = None,
    ) -> Dataset:
        """
        Ray-native processing for pull_latest operations with deduplication.
        Args:
            ds: Ray Dataset to process
            join_key_columns: List of join key columns
            timestamp_field: Name of the timestamp field
            created_timestamp_column: Optional created timestamp column
            field_mapping: Optional field mapping dictionary
        Returns:
            Ray Dataset with latest records only
        """
        if not join_key_columns:
            return ds

        timestamp_field_mapped = (
            field_mapping.get(timestamp_field, timestamp_field)
            if field_mapping
            else timestamp_field
        )
        created_timestamp_column_mapped = (
            field_mapping.get(created_timestamp_column, created_timestamp_column)
            if field_mapping and created_timestamp_column
            else created_timestamp_column
        )

        timestamp_columns = [timestamp_field_mapped]
        if created_timestamp_column_mapped:
            timestamp_columns.append(created_timestamp_column_mapped)

        def deduplicate_batch(batch: pd.DataFrame) -> pd.DataFrame:
            if batch.empty:
                return batch

            existing_timestamp_columns = [
                col for col in timestamp_columns if col in batch.columns
            ]

            sort_columns = join_key_columns + existing_timestamp_columns
            if sort_columns:
                batch = batch.sort_values(
                    sort_columns,
                    ascending=[True] * len(join_key_columns)
                    + [False] * len(existing_timestamp_columns),
                )
                batch = batch.drop_duplicates(subset=join_key_columns, keep="first")

            return batch

        return ds.map_batches(deduplicate_batch, batch_format="pandas")

    @staticmethod
    def pull_latest_from_table_or_query(
        config: RepoConfig,
        data_source: DataSource,
        join_key_columns: List[str],
        feature_name_columns: List[str],
        timestamp_field: str,
        created_timestamp_column: Optional[str],
        start_date: datetime,
        end_date: datetime,
    ) -> RetrievalJob:
        store = RayOfflineStore()
        store._init_ray(config)

        source_path = store._get_source_path(data_source, config)

        def _load_ray_dataset():
            ds = store._load_and_filter_dataset_ray(
                source_path,
                data_source,
                join_key_columns,
                feature_name_columns,
                timestamp_field,
                created_timestamp_column,
                start_date,
                end_date,
            )
            field_mapping = getattr(data_source, "field_mapping", None)
            ds = store._pull_latest_processing_ray(
                ds,
                join_key_columns,
                timestamp_field,
                created_timestamp_column,
                field_mapping,
            )

            return ds

        def _load_pandas_fallback():
            return store._load_and_filter_dataset(
                source_path,
                data_source,
                join_key_columns,
                feature_name_columns,
                timestamp_field,
                created_timestamp_column,
                start_date,
                end_date,
            )

        try:
            return RayRetrievalJob(
                _load_ray_dataset,
                staging_location=config.offline_store.storage_path,
                config=config.offline_store,
            )
        except Exception as e:
            logger.warning(f"Ray-native processing failed: {e}, falling back to pandas")
            return RayRetrievalJob(
                _load_pandas_fallback,
                staging_location=config.offline_store.storage_path,
                config=config.offline_store,
            )

    @staticmethod
    def pull_all_from_table_or_query(
        config: RepoConfig,
        data_source: DataSource,
        join_key_columns: List[str],
        feature_name_columns: List[str],
        timestamp_field: str,
        created_timestamp_column: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> RetrievalJob:
        store = RayOfflineStore()
        store._init_ray(config)

        source_path = store._get_source_path(data_source, config)

        fs, path_in_fs = fsspec.core.url_to_fs(source_path)
        if not fs.exists(path_in_fs):
            raise FileNotFoundError(f"Parquet path does not exist: {source_path}")

        def _load_ray_dataset():
            return store._load_and_filter_dataset_ray(
                source_path,
                data_source,
                join_key_columns,
                feature_name_columns,
                timestamp_field,
                created_timestamp_column,
                start_date,
                end_date,
            )

        def _load_pandas_fallback():
            return store._load_and_filter_dataset(
                source_path,
                data_source,
                join_key_columns,
                feature_name_columns,
                timestamp_field,
                created_timestamp_column,
                start_date,
                end_date,
            )

        try:
            return RayRetrievalJob(
                _load_ray_dataset,
                staging_location=config.offline_store.storage_path,
                config=config.offline_store,
            )
        except Exception as e:
            logger.warning(f"Ray-native processing failed: {e}, falling back to pandas")
            return RayRetrievalJob(
                _load_pandas_fallback,
                staging_location=config.offline_store.storage_path,
                config=config.offline_store,
            )

    @staticmethod
    def write_logged_features(
        config: RepoConfig,
        data: Union[pa.Table, Path],
        source: LoggingSource,
        logging_config: LoggingConfig,
        registry: BaseRegistry,
    ) -> None:
        RayOfflineStore._ensure_ray_initialized(config)

        ray_config = getattr(config, "offline_store", None)
        if (
            ray_config
            and isinstance(ray_config, RayOfflineStoreConfig)
            and not ray_config.enable_ray_logging
        ):
            RayOfflineStore._suppress_ray_logging()

        destination = logging_config.destination
        assert isinstance(destination, FileLoggingDestination), (
            f"Ray offline store only supports FileLoggingDestination for logging, "
            f"got {type(destination)}"
        )

        repo_path = getattr(config, "repo_path", None) or os.getcwd()
        absolute_path = FileSource.get_uri_for_file_path(repo_path, destination.path)

        try:
            ray_wrapper = get_ray_wrapper()
            if isinstance(data, Path):
                ds = ray_wrapper.read_parquet(str(data))
            else:
                ds = ray_wrapper.from_arrow(data)

                # Normalize feature timestamp precision to seconds to match test expectations during write
                # Note: Don't normalize __log_timestamp as it's used for time range filtering
                def normalize_timestamps(batch: pd.DataFrame) -> pd.DataFrame:
                    batch = batch.copy()
                    for col in batch.columns:
                        if (
                            pd.api.types.is_datetime64_any_dtype(batch[col])
                            and col != "__log_timestamp"
                        ):
                            batch[col] = batch[col].dt.floor("s")
                    return batch

                ds = ds.map_batches(normalize_timestamps, batch_format="pandas")
            ds = ds.materialize()
            filesystem, resolved_path = FileSource.create_filesystem_and_path(
                absolute_path, destination.s3_endpoint_override
            )
            if absolute_path.startswith(REMOTE_STORAGE_SCHEMES):
                write_path = (
                    absolute_path[:-8]
                    if absolute_path.endswith(".parquet")
                    else absolute_path
                )
            else:
                path_obj = Path(resolved_path)
                if path_obj.suffix == ".parquet":
                    path_obj = path_obj.with_suffix("")
                path_obj.mkdir(parents=True, exist_ok=True)
                write_path = str(path_obj)
            ds.write_parquet(write_path)
        except Exception as e:
            raise RuntimeError(f"Failed to write logged features: {e}")

    @staticmethod
    def create_saved_dataset_destination(
        config: RepoConfig,
        name: str,
        path: Optional[str] = None,
    ) -> SavedDatasetStorage:
        """Create a saved dataset destination for Ray offline store."""

        if path is None:
            ray_config = config.offline_store
            assert isinstance(ray_config, RayOfflineStoreConfig)
            base_storage_path = ray_config.storage_path or "/tmp/ray-storage"
            path = f"{base_storage_path}/saved_datasets/{name}.parquet"

        return SavedDatasetFileStorage(path=path)

    @staticmethod
    def _create_filtered_dataset(
        source_path: str,
        timestamp_field: str,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        columns: Optional[List[str]] = None,
    ) -> Dataset:
        """Helper method to create a filtered dataset based on timestamp range."""
        ray_wrapper = get_ray_wrapper()
        ds = ray_wrapper.read_parquet(source_path, columns=columns)

        try:
            col_names = ds.schema().names
            if timestamp_field not in col_names:
                raise ValueError(
                    f"Timestamp field '{timestamp_field}' not found in columns: {col_names}"
                )
        except Exception as e:
            raise ValueError(f"Failed to get dataset schema: {e}")

        def normalize(dt):
            return make_tzaware(dt) if dt and dt.tzinfo is None else dt

        start_date = normalize(start_date)
        end_date = normalize(end_date)

        try:
            if start_date and end_date:

                def filter_by_timestamp_range(batch):
                    return (batch[timestamp_field] >= start_date) & (
                        batch[timestamp_field] <= end_date
                    )

                ds = ds.filter(filter_by_timestamp_range)
            elif start_date:

                def filter_by_start_date(batch):
                    return batch[timestamp_field] >= start_date

                ds = ds.filter(filter_by_start_date)
            elif end_date:

                def filter_by_end_date(batch):
                    return batch[timestamp_field] <= end_date

                ds = ds.filter(filter_by_end_date)
        except Exception as e:
            raise RuntimeError(f"Failed to filter dataset by timestamp: {e}")

        return ds

    @staticmethod
    def get_historical_features(
        config: RepoConfig,
        feature_views: List[FeatureView],
        feature_refs: List[str],
        entity_df: Optional[Union[pd.DataFrame, str]],
        registry: BaseRegistry,
        project: str,
        full_feature_names: bool = False,
        **kwargs: Any,
    ) -> RetrievalJob:
        store = RayOfflineStore()
        store._init_ray(config)

        # Load or derive entity dataset for distributed processing
        ray_wrapper = get_ray_wrapper()
        if entity_df is None:
            # Non-entity mode: derive entity set from feature sources within a bounded time window
            # Preserves distinct (entity_keys, event_timestamp) combinations for proper PIT joins
            # This handles cases where multiple transactions per entity ID exist
            start_date, end_date = _compute_non_entity_dates_ray(
                feature_views, kwargs.get("start_date"), kwargs.get("end_date")
            )
            per_view_entity_ds: List[Dataset] = []
            all_join_keys: List[str] = []
            for fv in feature_views:
                ds, join_keys = _distinct_entities_for_feature_view_ray(
                    store, config, fv, registry, project, start_date, end_date
                )
                per_view_entity_ds.append(ds)
                for k in join_keys:
                    if k not in all_join_keys:
                        all_join_keys.append(k)
            # Use include_timestamp=True to preserve actual event_timestamp from data
            # instead of assigning a fixed end_date to all entities
            entity_ds = _align_and_union_entities_ray(
                per_view_entity_ds, all_join_keys, include_timestamp=True
            )
            entity_df_sample = entity_ds.limit(1000).to_pandas()
        elif isinstance(entity_df, str):
            entity_ds = ray_wrapper.read_csv(entity_df)
            entity_df_sample = entity_ds.limit(1000).to_pandas()
        else:
            entity_ds = ray_wrapper.from_pandas(entity_df)
            entity_df_sample = entity_df.copy()

        entity_ds = ensure_timestamp_compatibility(entity_ds, ["event_timestamp"])
        on_demand_feature_views = OnDemandFeatureView.get_requested_odfvs(
            feature_refs, project, registry
        )
        for odfv in on_demand_feature_views:
            odfv_request_data_schema = odfv.get_request_data_schema()
            for feature_name in odfv_request_data_schema.keys():
                if feature_name not in entity_df_sample.columns:
                    raise RequestDataNotFoundInEntityDfException(
                        feature_name=feature_name,
                        feature_view_name=odfv.name,
                    )

        odfv_names = {odfv.name for odfv in on_demand_feature_views}
        regular_feature_views = [
            fv for fv in feature_views if fv.name not in odfv_names
        ]
        global_field_mappings = {}
        for fv in regular_feature_views:
            mapping = getattr(fv.batch_source, "field_mapping", None)
            if mapping:
                for k, v in mapping.items():
                    global_field_mappings[v] = k

        if global_field_mappings:
            cols_to_rename = {
                v: k
                for k, v in global_field_mappings.items()
                if v in entity_df_sample.columns
            }
            if cols_to_rename:
                entity_ds = apply_field_mapping(entity_ds, cols_to_rename)

        result_ds = entity_ds
        for fv in regular_feature_views:
            fv_feature_refs = [
                ref
                for ref in feature_refs
                if ref.startswith(fv.projection.name_to_use() + ":")
            ]
            if not fv_feature_refs:
                continue

            entities = fv.entities or []
            entity_objs = [registry.get_entity(e, project) for e in entities]
            (
                original_join_keys,
                reverse_mapped_feature_names,
                timestamp_field,
                created_col,
            ) = _get_column_names(fv, entity_objs)

            if fv.projection.join_key_map:
                join_keys = [
                    fv.projection.join_key_map.get(key, key)
                    for key in original_join_keys
                ]
            else:
                join_keys = original_join_keys

            # Get the logical feature names from refs
            logical_requested_feats = [ref.split(":", 1)[1] for ref in fv_feature_refs]

            available_feature_names = [f.name for f in fv.features]
            missing_feats = [
                f for f in logical_requested_feats if f not in available_feature_names
            ]
            if missing_feats:
                raise KeyError(
                    f"Requested features {missing_feats} not found in feature view '{fv.name}' "
                    f"(available: {available_feature_names})"
                )

            # Build reverse field mapping to get actual source column names
            reverse_field_mapping = {}
            if fv.batch_source is not None and fv.batch_source.field_mapping:
                reverse_field_mapping = {
                    v: k for k, v in fv.batch_source.field_mapping.items()
                }

            # Map logical feature names to actual source column names
            requested_feats = [
                reverse_field_mapping.get(feat, feat)
                for feat in logical_requested_feats
            ]

            source_info = resolve_feature_view_source_with_fallback(
                fv, config, is_materialization=False
            )

            # Read from the resolved data source
            source_path = store._get_source_path(source_info.data_source, config)

            if not source_info.has_transformation:
                required_feature_columns = set(
                    original_join_keys + requested_feats + [timestamp_field]
                )
                if created_col:
                    required_feature_columns.add(created_col)
                feature_ds = ray_wrapper.read_parquet(
                    source_path, columns=list(required_feature_columns)
                )
            else:
                feature_ds = ray_wrapper.read_parquet(source_path)

            # Apply transformation if available
            if source_info.has_transformation and source_info.transformation_func:
                transformation_serialized = dill.dumps(source_info.transformation_func)

                def apply_transformation_with_serialized_func(
                    batch: pd.DataFrame,
                ) -> pd.DataFrame:
                    if batch.empty:
                        return batch
                    try:
                        logger.debug(
                            f"Applying transformation to batch with columns: {list(batch.columns)}"
                        )
                        transformation_func = dill.loads(transformation_serialized)
                        result = transformation_func(batch)
                        logger.debug(
                            f"Transformation result has columns: {list(result.columns)}"
                        )
                        return result
                    except Exception as e:
                        logger.error(f"Transformation failed for {fv.name}: {e}")
                        return batch

                feature_ds = feature_ds.map_batches(
                    apply_transformation_with_serialized_func, batch_format="pandas"
                )
                logger.info(f"Applied transformation to feature view {fv.name}")
            elif source_info.has_transformation:
                logger.warning(
                    f"Feature view {fv.name} marked as having transformation but no UDF found"
                )

            feature_size = feature_ds.size_bytes() or 0

            field_mapping = getattr(fv.batch_source, "field_mapping", None)
            if field_mapping:
                feature_ds = apply_field_mapping(feature_ds, field_mapping)
                # Update original_join_keys to logical names after forward mapping
                original_join_keys = [
                    field_mapping.get(k, k) for k in original_join_keys
                ]
                # Recompute join_keys from updated original_join_keys
                if fv.projection.join_key_map:
                    join_keys = [
                        fv.projection.join_key_map.get(key, key)
                        for key in original_join_keys
                    ]
                else:
                    join_keys = original_join_keys
                timestamp_field = field_mapping.get(timestamp_field, timestamp_field)
                if created_col:
                    created_col = field_mapping.get(created_col, created_col)
                # Also map requested_feats back to logical names after forward mapping
                requested_feats = [field_mapping.get(f, f) for f in requested_feats]

            if (
                timestamp_field != "event_timestamp"
                and timestamp_field not in entity_df_sample.columns
                and "event_timestamp" in entity_df_sample.columns
            ):

                def add_timestamp_field(batch: pd.DataFrame) -> pd.DataFrame:
                    batch = batch.copy()
                    batch[timestamp_field] = batch["event_timestamp"]
                    return batch

                result_ds = result_ds.map_batches(
                    add_timestamp_field, batch_format="pandas"
                )
                result_ds = normalize_timestamp_columns(result_ds, timestamp_field)

            if store._resource_manager is None:
                raise ValueError("Resource manager not initialized")
            requirements = store._resource_manager.estimate_processing_requirements(
                feature_size, "join"
            )

            if requirements["should_broadcast"]:
                # Use broadcast join for small feature datasets
                if getattr(store._resource_manager.config, "enable_ray_logging", False):
                    logger.info(
                        f"Using broadcast join for {fv.name} (size: {feature_size // 1024**2}MB)"
                    )
                feature_df = feature_ds.to_pandas()
                feature_df = ensure_timestamp_compatibility(
                    feature_df, [timestamp_field]
                )

                if store._data_processor is None:
                    raise ValueError("Data processor not initialized")
                result_ds = store._data_processor.broadcast_join_features(
                    result_ds,
                    feature_df,
                    join_keys,
                    timestamp_field,
                    requested_feats,
                    full_feature_names,
                    fv.projection.name_to_use(),
                    original_join_keys if fv.projection.join_key_map else None,
                )
            else:
                # Use distributed windowed join for large feature datasets
                if getattr(store._resource_manager.config, "enable_ray_logging", False):
                    logger.info(
                        f"Using distributed join for {fv.name} (size: {feature_size // 1024**2}MB)"
                    )
                feature_ds = ensure_timestamp_compatibility(
                    feature_ds, [timestamp_field]
                )

                if store._data_processor is None:
                    raise ValueError("Data processor not initialized")
                result_ds = store._data_processor.windowed_temporal_join(
                    result_ds,
                    feature_ds,
                    join_keys,
                    timestamp_field,
                    requested_feats,
                    window_size=config.offline_store.window_size_for_joins,
                    full_feature_names=full_feature_names,
                    feature_view_name=fv.projection.name_to_use(),
                    original_join_keys=original_join_keys
                    if fv.projection.join_key_map
                    else None,
                )

        def finalize_result(batch: pd.DataFrame) -> pd.DataFrame:
            batch = batch.copy()

            existing_columns = set(batch.columns)
            for col in entity_df_sample.columns:
                if col not in existing_columns:
                    if len(batch) <= len(entity_df_sample):
                        batch[col] = entity_df_sample[col].iloc[: len(batch)].values
                    else:
                        repeated_values = np.tile(
                            entity_df_sample[col].values,
                            (len(batch) // len(entity_df_sample) + 1),
                        )
                        batch[col] = repeated_values[: len(batch)]

            if "event_timestamp" not in batch.columns:
                if "event_timestamp" in entity_df_sample.columns:
                    batch["event_timestamp"] = (
                        entity_df_sample["event_timestamp"].iloc[: len(batch)].values
                    )
                    batch = normalize_timestamp_columns(
                        batch, "event_timestamp", inplace=True
                    )
                elif timestamp_field in batch.columns:
                    batch["event_timestamp"] = batch[timestamp_field]

            return batch

        result_ds = result_ds.map_batches(finalize_result, batch_format="pandas")
        result_ds = _convert_feature_column_types(result_ds, regular_feature_views)

        storage_path = config.offline_store.storage_path
        if not storage_path:
            raise ValueError("Storage path must be set in config")

        job = RayRetrievalJob(
            result_ds, staging_location=storage_path, config=config.offline_store
        )
        job._full_feature_names = full_feature_names
        job._on_demand_feature_views = on_demand_feature_views
        job._feature_refs = feature_refs
        job._entity_df = entity_df_sample
        job._metadata = job._create_metadata()
        return job
