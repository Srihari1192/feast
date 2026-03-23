import logging
from typing import List, Optional

import numpy as np
import pandas as pd
import ray
from ray.data import Dataset

from feast.infra.ray_shared_utils import normalize_timestamp_columns

from .config import RayResourceManager

logger = logging.getLogger(__name__)


class RayDataProcessor:
    """
    Optimized data processing with Ray for feature store operations.
    """

    def __init__(self, resource_manager: RayResourceManager) -> None:
        """
        Initialize the data processor with a resource manager.
        """
        self.resource_manager = resource_manager

    def optimize_dataset_for_join(self, ds: Dataset, join_keys: List[str]) -> Dataset:
        """
        Optimize dataset partitioning for join operations.
        """
        dataset_size = ds.size_bytes()
        optimal_partitions = self.resource_manager.estimate_optimal_partitions(
            dataset_size
        )
        if not join_keys:
            # For datasets without join keys, use simple repartitioning
            return ds.repartition(num_blocks=optimal_partitions)
        # For datasets with join keys, repartition then shuffle for better distribution
        return ds.repartition(num_blocks=optimal_partitions).random_shuffle()

    def _manual_point_in_time_join(
        self,
        batch_df: pd.DataFrame,
        features_df: pd.DataFrame,
        join_keys: List[str],
        feature_join_keys: List[str],
        timestamp_field: str,
        requested_feats: List[str],
    ) -> pd.DataFrame:
        """
        Perform manual point-in-time join when merge_asof fails.

        This method handles cases where merge_asof cannot be used due to:
        - Entity mapping (different column names)
        - Complex multi-entity joins
        - Sorting issues with the data
        """
        result = batch_df.copy()
        for feat in requested_feats:
            is_list_feature = False
            if feat in features_df.columns:
                sample_values = features_df[feat].dropna()
                if not sample_values.empty:
                    sample_value = sample_values.iloc[0]
                    if isinstance(sample_value, (list, np.ndarray)):
                        is_list_feature = True
                    elif (
                        features_df[feat].dtype == object
                        and sample_values.apply(
                            lambda x: isinstance(x, (list, np.ndarray))
                        ).any()
                    ):
                        is_list_feature = True

            if is_list_feature:
                result[feat] = [[] for _ in range(len(result))]
            else:
                if feat in features_df.columns and pd.api.types.is_datetime64_any_dtype(
                    features_df[feat]
                ):
                    result[feat] = pd.Series(
                        [pd.NaT] * len(result), dtype="datetime64[ns, UTC]"
                    )
                else:
                    result[feat] = np.nan

        for _, entity_row in batch_df.iterrows():
            entity_matches = pd.Series(
                [True] * len(features_df), index=features_df.index
            )
            for entity_key, feature_key in zip(join_keys, feature_join_keys):
                if entity_key in entity_row and feature_key in features_df.columns:
                    entity_value = entity_row[entity_key]
                    feature_column = features_df[feature_key]
                    if pd.api.types.is_scalar(entity_value):
                        entity_matches &= feature_column == entity_value
                    else:
                        if hasattr(entity_value, "__len__") and len(entity_value) > 0:
                            entity_matches &= feature_column.isin(entity_value)
                        else:
                            entity_matches &= pd.Series(
                                [False] * len(features_df), index=features_df.index
                            )
            if not entity_matches.any():
                continue
            matching_features = features_df[entity_matches]
            entity_timestamp = entity_row[timestamp_field]
            if timestamp_field in matching_features.columns:
                time_matches = matching_features[timestamp_field] <= entity_timestamp
                matching_features = matching_features[time_matches]
            if matching_features.empty:
                continue

            if timestamp_field in matching_features.columns:
                matching_features = matching_features.sort_values(timestamp_field)
                latest_feature = matching_features.iloc[-1]
            else:
                latest_feature = matching_features.iloc[-1]

            entity_index = entity_row.name
            for feat in requested_feats:
                if feat in latest_feature:
                    feature_value = latest_feature[feat]
                    if pd.api.types.is_scalar(feature_value):
                        if pd.notna(feature_value):
                            result.loc[entity_index, feat] = feature_value
                    elif isinstance(feature_value, (list, tuple, np.ndarray)):
                        result.at[entity_index, feat] = feature_value
                    else:
                        try:
                            if pd.notna(feature_value):
                                result.at[entity_index, feat] = feature_value
                        except (ValueError, TypeError):
                            if feature_value is not None:
                                result.at[entity_index, feat] = feature_value

        return result

    def broadcast_join_features(
        self,
        entity_ds: Dataset,
        feature_df: pd.DataFrame,
        join_keys: List[str],
        timestamp_field: str,
        requested_feats: List[str],
        full_feature_names: bool = False,
        feature_view_name: Optional[str] = None,
        original_join_keys: Optional[List[str]] = None,
    ) -> Dataset:
        """Perform broadcast join for small feature datasets."""

        # Put feature data in Ray object store for efficient broadcasting
        feature_ref = ray.put(feature_df)

        def join_batch_with_features(batch: pd.DataFrame) -> pd.DataFrame:
            """Join a batch with broadcast feature data."""
            features = ray.get(feature_ref)

            enable_logging = getattr(
                self.resource_manager.config, "enable_ray_logging", False
            )
            if enable_logging:
                logger.info(
                    f"Processing feature view {feature_view_name} with join keys {join_keys}"
                )

            if original_join_keys:
                feature_join_keys = original_join_keys
                entity_join_keys = join_keys
            else:
                feature_join_keys = join_keys
                entity_join_keys = join_keys

            feature_cols = [timestamp_field] + feature_join_keys + requested_feats

            available_feature_cols = [
                col for col in feature_cols if col in features.columns
            ]

            if timestamp_field not in available_feature_cols:
                raise ValueError(
                    f"Timestamp field '{timestamp_field}' not found in features columns: {list(features.columns)}"
                )

            missing_feats = [
                feat for feat in requested_feats if feat not in features.columns
            ]
            if missing_feats:
                raise ValueError(
                    f"Requested features {missing_feats} not found in features columns: {list(features.columns)}"
                )

            features_filtered = features[available_feature_cols].copy()

            batch = normalize_timestamp_columns(batch, timestamp_field, inplace=True)
            features_filtered = normalize_timestamp_columns(
                features_filtered, timestamp_field, inplace=True
            )

            if not entity_join_keys:
                batch_sorted = batch.sort_values(timestamp_field).reset_index(drop=True)
                features_sorted = features_filtered.sort_values(
                    timestamp_field
                ).reset_index(drop=True)
                result = pd.merge_asof(
                    batch_sorted,
                    features_sorted,
                    on=timestamp_field,
                    direction="backward",
                )
            else:
                for key in entity_join_keys:
                    if key not in batch.columns:
                        batch[key] = np.nan
                for key in feature_join_keys:
                    if key not in features_filtered.columns:
                        features_filtered[key] = np.nan
                batch_clean = batch.dropna(
                    subset=entity_join_keys + [timestamp_field]
                ).copy()
                features_clean = features_filtered.dropna(
                    subset=feature_join_keys + [timestamp_field]
                ).copy()
                if batch_clean.empty or features_clean.empty:
                    return batch.head(0)
                if timestamp_field in batch_clean.columns:
                    batch_sorted = batch_clean.sort_values(
                        timestamp_field, ascending=True
                    ).reset_index(drop=True)
                else:
                    batch_sorted = batch_clean.reset_index(drop=True)

                right_sort_columns = []
                for key in feature_join_keys:
                    if key in features_clean.columns:
                        right_sort_columns.append(key)
                if timestamp_field in features_clean.columns:
                    right_sort_columns.append(timestamp_field)
                if right_sort_columns:
                    features_clean = features_clean.drop_duplicates(
                        subset=right_sort_columns, keep="last"
                    )
                    features_sorted = features_clean.sort_values(
                        right_sort_columns, ascending=True
                    ).reset_index(drop=True)
                else:
                    features_sorted = features_clean.reset_index(drop=True)

                if (
                    timestamp_field in features_sorted.columns
                    and len(features_sorted) > 1
                ):
                    if feature_join_keys:
                        grouped = features_sorted.groupby(feature_join_keys, sort=False)
                        for name, group in grouped:
                            if not group[timestamp_field].is_monotonic_increasing:
                                features_sorted = features_sorted.sort_values(
                                    feature_join_keys + [timestamp_field],
                                    ascending=True,
                                ).reset_index(drop=True)
                                break
                    else:
                        if not features_sorted[timestamp_field].is_monotonic_increasing:
                            features_sorted = features_sorted.sort_values(
                                timestamp_field, ascending=True
                            ).reset_index(drop=True)

                try:
                    if feature_join_keys:
                        batch_dedup_cols = [
                            k for k in entity_join_keys if k in batch_sorted.columns
                        ]
                        if timestamp_field in batch_sorted.columns:
                            batch_dedup_cols.append(timestamp_field)
                        if batch_dedup_cols:
                            batch_sorted = batch_sorted.drop_duplicates(
                                subset=batch_dedup_cols, keep="last"
                            )
                        feature_dedup_cols = [
                            k for k in feature_join_keys if k in features_sorted.columns
                        ]
                        if timestamp_field in features_sorted.columns:
                            feature_dedup_cols.append(timestamp_field)
                        if feature_dedup_cols:
                            features_sorted = features_sorted.drop_duplicates(
                                subset=feature_dedup_cols, keep="last"
                            )

                    if feature_join_keys:
                        if entity_join_keys == feature_join_keys:
                            result = pd.merge_asof(
                                batch_sorted,
                                features_sorted,
                                on=timestamp_field,
                                by=entity_join_keys,
                                direction="backward",
                                suffixes=("", "_right"),
                            )
                        else:
                            result = pd.merge_asof(
                                batch_sorted,
                                features_sorted,
                                on=timestamp_field,
                                left_by=entity_join_keys,
                                right_by=feature_join_keys,
                                direction="backward",
                                suffixes=("", "_right"),
                            )
                    else:
                        result = pd.merge_asof(
                            batch_sorted,
                            features_sorted,
                            on=timestamp_field,
                            direction="backward",
                            suffixes=("", "_right"),
                        )

                except Exception as e:
                    if enable_logging:
                        logger.warning(
                            f"merge_asof didn't work: {e}, implementing manual point-in-time join"
                        )
                    result = self._manual_point_in_time_join(
                        batch_clean,
                        features_clean,
                        entity_join_keys,
                        feature_join_keys,
                        timestamp_field,
                        requested_feats,
                    )
            if full_feature_names and feature_view_name:
                for feat in requested_feats:
                    if feat in result.columns:
                        new_name = f"{feature_view_name}__{feat}"
                        result[new_name] = result[feat]
                        result = result.drop(columns=[feat])

            return result

        return entity_ds.map_batches(join_batch_with_features, batch_format="pandas")

    def windowed_temporal_join(
        self,
        entity_ds: Dataset,
        feature_ds: Dataset,
        join_keys: List[str],
        timestamp_field: str,
        requested_feats: List[str],
        window_size: Optional[str] = None,
        full_feature_names: bool = False,
        feature_view_name: Optional[str] = None,
        original_join_keys: Optional[List[str]] = None,
    ) -> Dataset:
        """Perform windowed temporal join for large datasets."""

        window_size = window_size or (
            self.resource_manager.config.window_size_for_joins or "1H"
        )
        entity_optimized = self.optimize_dataset_for_join(entity_ds, join_keys)
        feature_optimized = self.optimize_dataset_for_join(feature_ds, join_keys)
        entity_windowed = self._add_time_windows_and_source_marker(
            entity_optimized, timestamp_field, "entity", window_size
        )
        feature_windowed = self._add_time_windows_and_source_marker(
            feature_optimized, timestamp_field, "feature", window_size
        )
        combined_ds = entity_windowed.union(feature_windowed)
        result_ds = combined_ds.map_batches(
            self._apply_windowed_point_in_time_logic,
            batch_format="pandas",
            fn_kwargs={
                "timestamp_field": timestamp_field,
                "join_keys": join_keys,
                "requested_feats": requested_feats,
                "full_feature_names": full_feature_names,
                "feature_view_name": feature_view_name,
                "original_join_keys": original_join_keys,
            },
        )

        return result_ds

    def _add_time_windows_and_source_marker(
        self, ds: Dataset, timestamp_field: str, source_marker: str, window_size: str
    ) -> Dataset:
        """Add time windows and source markers to dataset."""

        def add_window_and_source(batch: pd.DataFrame) -> pd.DataFrame:
            batch = batch.copy()
            if timestamp_field in batch.columns:
                batch["time_window"] = (
                    pd.to_datetime(batch[timestamp_field])
                    .dt.floor(window_size)
                    .astype("datetime64[ns, UTC]")
                )
            batch["_data_source"] = source_marker
            return batch

        return ds.map_batches(add_window_and_source, batch_format="pandas")

    def _apply_windowed_point_in_time_logic(
        self,
        batch: pd.DataFrame,
        timestamp_field: str,
        join_keys: List[str],
        requested_feats: List[str],
        full_feature_names: bool = False,
        feature_view_name: Optional[str] = None,
        original_join_keys: Optional[List[str]] = None,
    ) -> pd.DataFrame:
        """Apply point-in-time correctness within time windows."""

        if len(batch) == 0:
            return pd.DataFrame()

        result_chunks = []
        group_keys = ["time_window"] + join_keys

        for group_values, group_data in batch.groupby(group_keys):
            entity_data = group_data[group_data["_data_source"] == "entity"].copy()
            feature_data = group_data[group_data["_data_source"] == "feature"].copy()
            if len(entity_data) > 0 and len(feature_data) > 0:
                entity_clean = entity_data.drop(columns=["time_window", "_data_source"])
                feature_clean = feature_data.drop(
                    columns=["time_window", "_data_source"]
                )
                if join_keys:
                    merged = pd.merge_asof(
                        entity_clean.sort_values(join_keys + [timestamp_field]),
                        feature_clean.sort_values(join_keys + [timestamp_field]),
                        on=timestamp_field,
                        by=join_keys,
                        direction="backward",
                    )
                else:
                    merged = pd.merge_asof(
                        entity_clean.sort_values(timestamp_field),
                        feature_clean.sort_values(timestamp_field),
                        on=timestamp_field,
                        direction="backward",
                    )

                result_chunks.append(merged)
            elif len(entity_data) > 0:
                entity_clean = entity_data.drop(columns=["time_window", "_data_source"])
                for feat in requested_feats:
                    if feat not in entity_clean.columns:
                        entity_clean[feat] = np.nan
                result_chunks.append(entity_clean)

        if result_chunks:
            result = pd.concat(result_chunks, ignore_index=True)
            if full_feature_names and feature_view_name:
                for feat in requested_feats:
                    if feat in result.columns:
                        new_name = f"{feature_view_name}__{feat}"
                        result[new_name] = result[feat]
                        result = result.drop(columns=[feat])

            return result
        else:
            return pd.DataFrame()
