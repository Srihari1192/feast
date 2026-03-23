import logging
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

import numpy as np
import pandas as pd
import pyarrow as pa
from ray.data import Dataset

from feast.data_source import DataSource
from feast.infra.offline_stores.offline_utils import (
    get_entity_df_timestamp_bounds,
    get_pyarrow_schema_from_batch_source,
    infer_event_timestamp_from_entity_df,
)
from feast.infra.ray_shared_utils import (
    _build_required_columns,
    is_ray_data,
)
from feast.repo_config import RepoConfig
from feast.type_map import (
    convert_array_column,
    convert_scalar_column,
    feast_value_type_to_pandas_type,
    pa_to_feast_value_type,
)

from feast.feature_view import FeatureView

logger = logging.getLogger(__name__)


def _get_data_schema_info(
    data: Union[pd.DataFrame, Dataset, Any],
) -> Tuple[Dict[str, Any], List[str]]:
    """
    Extract schema information from DataFrame or Dataset.
    Args:
        data: DataFrame or Ray Dataset
    Returns:
        Tuple of (dtypes_dict, column_names)
    """
    if is_ray_data(data):
        schema = data.schema()
        dtypes = {}
        for i, col in enumerate(schema.names):
            field_type = schema.field(i).type
            try:
                pa_type_str = str(field_type).lower()
                feast_value_type = pa_to_feast_value_type(pa_type_str)
                pandas_type_str = feast_value_type_to_pandas_type(feast_value_type)
                dtypes[col] = pd.api.types.pandas_dtype(pandas_type_str)
            except Exception:
                dtypes[col] = pd.api.types.pandas_dtype("object")
        columns = schema.names
    else:
        assert isinstance(data, pd.DataFrame)
        dtypes = data.dtypes.to_dict()
        columns = list(data.columns)
    return dtypes, columns


def _apply_to_data(
    data: Union[pd.DataFrame, Dataset, Any],
    process_func: Callable[[pd.DataFrame], pd.DataFrame],
    inplace: bool = False,
) -> Union[pd.DataFrame, Dataset, Any]:
    """
    Apply a processing function to DataFrame or Dataset.
    Args:
        data: DataFrame or Ray Dataset to process
        process_func: Function that takes a DataFrame and returns a processed DataFrame
        inplace: Whether to modify DataFrame in place (only applies to pandas)
    Returns:
        Processed DataFrame or Dataset
    """
    if is_ray_data(data):
        return data.map_batches(process_func, batch_format="pandas")
    else:
        assert isinstance(data, pd.DataFrame)
        if not inplace:
            data = data.copy()
        return process_func(data)


def _handle_empty_dataframe_case(
    join_key_columns: List[str],
    feature_name_columns: List[str],
    timestamp_columns: List[str],
) -> pd.DataFrame:
    """
    Handle empty DataFrame case by creating properly structured empty DataFrame.
    Args:
        join_key_columns: List of join key columns
        feature_name_columns: List of feature columns
        timestamp_columns: List of timestamp columns
    Returns:
        Empty DataFrame with proper structure and column types
    """
    empty_columns = _build_required_columns(
        join_key_columns, feature_name_columns, timestamp_columns
    )
    df = pd.DataFrame(columns=empty_columns)
    for col in timestamp_columns:
        if col in df.columns:
            df[col] = df[col].astype("datetime64[ns, UTC]")
    return df


def _safe_infer_event_timestamp_column(
    data: Union[pd.DataFrame, Dataset], fallback_column: str = "event_timestamp"
) -> str:
    """
    Safely infer the event timestamp column.
    Works with both pandas DataFrames and Ray Datasets.
    Args:
        data: DataFrame or Ray Dataset to analyze
        fallback_column: Default column name to use if inference fails
    Returns:
        Inferred or fallback timestamp column name
    """
    try:
        dtypes, _ = _get_data_schema_info(data)
        return infer_event_timestamp_from_entity_df(dtypes)
    except Exception as e:
        logger.debug(
            f"Timestamp column inference failed: {e}, using fallback: {fallback_column}"
        )
        return fallback_column


def _safe_get_entity_timestamp_bounds(
    data: Union[pd.DataFrame, Dataset, Any], timestamp_column: str
) -> Tuple[Optional[datetime], Optional[datetime]]:
    """
    Safely get entity timestamp bounds.
    Works with both pandas DataFrames and Ray Datasets.
    Args:
        data: DataFrame or Ray Dataset
        timestamp_column: Name of timestamp column
    Returns:
        Tuple of (min_timestamp, max_timestamp) or (None, None) if failed
    """
    try:
        if is_ray_data(data):
            min_ts = data.min(timestamp_column)
            max_ts = data.max(timestamp_column)
        else:
            if timestamp_column in data.columns:
                min_ts, max_ts = get_entity_df_timestamp_bounds(data, timestamp_column)
            else:
                return None, None
        if hasattr(min_ts, "to_pydatetime"):
            min_ts = min_ts.to_pydatetime()
        elif isinstance(min_ts, pd.Timestamp):
            min_ts = min_ts.to_pydatetime()
        if hasattr(max_ts, "to_pydatetime"):
            max_ts = max_ts.to_pydatetime()
        elif isinstance(max_ts, pd.Timestamp):
            max_ts = max_ts.to_pydatetime()
        return min_ts, max_ts
    except Exception as e:
        logger.debug(
            f"Timestamp bounds extraction failed: {e}, falling back to manual calculation"
        )
        try:
            if is_ray_data(data):

                def extract_bounds(batch: pd.DataFrame) -> pd.DataFrame:
                    if timestamp_column in batch.columns and not batch.empty:
                        timestamps = pd.to_datetime(batch[timestamp_column], utc=True)
                        return pd.DataFrame(
                            {"min_ts": [timestamps.min()], "max_ts": [timestamps.max()]}
                        )
                    return pd.DataFrame({"min_ts": [None], "max_ts": [None]})

                bounds_ds = data.map_batches(extract_bounds, batch_format="pandas")
                bounds_df = bounds_ds.to_pandas()

                if not bounds_df.empty:
                    min_ts = bounds_df["min_ts"].min()
                    max_ts = bounds_df["max_ts"].max()

                    if pd.notna(min_ts) and pd.notna(max_ts):
                        return min_ts.to_pydatetime(), max_ts.to_pydatetime()
            else:
                assert isinstance(data, pd.DataFrame)
                if timestamp_column in data.columns:
                    timestamps = pd.to_datetime(data[timestamp_column], utc=True)
                    return (
                        timestamps.min().to_pydatetime(),
                        timestamps.max().to_pydatetime(),
                    )
        except Exception:
            pass

        return None, None


def _safe_validate_schema(
    config: RepoConfig,
    data_source: DataSource,
    table_columns: List[str],
    operation_name: str = "operation",
) -> Optional[Tuple[pa.Schema, List[str]]]:
    """
    Safely validate schema using offline_utils with graceful fallback.
    Args:
        config: Repo configuration
        data_source: Data source to validate against
        table_columns: Actual table column names
        operation_name: Name of operation for logging
    Returns:
        Tuple of (expected_schema, expected_columns) or None if validation fails
    """
    try:
        expected_schema, expected_columns = get_pyarrow_schema_from_batch_source(
            config, data_source
        )
        if set(expected_columns) != set(table_columns):
            logger.warning(
                f"Schema mismatch in {operation_name}:\n"
                f"  Expected columns: {expected_columns}\n"
                f"  Actual columns: {table_columns}"
            )
            if set(expected_columns) == set(table_columns):
                logger.info(f"Columns match but order differs for {operation_name}")
                return expected_schema, expected_columns
        else:
            logger.debug(f"Schema validation passed for {operation_name}")
            return expected_schema, expected_columns

    except Exception as e:
        logger.warning(
            f"Schema validation skipped for {operation_name} due to error: {e}"
        )
        logger.debug("Schema validation error details:", exc_info=True)
    return None


def _convert_feature_column_types(
    data: Union[pd.DataFrame, Dataset], feature_views: List[FeatureView]
) -> Union[pd.DataFrame, Dataset]:
    """
    Convert feature columns to appropriate pandas types using Feast's type mapping utilities.
    Works with both pandas DataFrames and Ray Datasets.
    Args:
        data: DataFrame or Ray Dataset containing feature data
        feature_views: List of feature views with type information
    Returns:
        DataFrame or Dataset with properly converted feature column types
    """

    def convert_batch(batch: pd.DataFrame) -> pd.DataFrame:
        batch = batch.copy()

        for fv in feature_views:
            for feature in fv.features:
                feat_name = feature.name
                if feat_name not in batch.columns:
                    continue
                try:
                    value_type = feature.dtype.to_value_type()
                    if value_type.name.endswith("_LIST"):
                        batch[feat_name] = convert_array_column(
                            batch[feat_name], value_type
                        )
                    else:
                        target_pandas_type = feast_value_type_to_pandas_type(value_type)
                        batch[feat_name] = convert_scalar_column(
                            batch[feat_name], value_type, target_pandas_type
                        )
                except Exception as e:
                    logger.warning(
                        f"Failed to convert feature {feat_name} to proper type: {e}"
                    )
                    continue
        return batch

    return _apply_to_data(data, convert_batch)
