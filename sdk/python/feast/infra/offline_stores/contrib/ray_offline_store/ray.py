"""
Backward-compatibility shim.

All classes have been moved to dedicated modules within this package:
- config.py: RayOfflineStoreConfig, RayResourceManager
- processor.py: RayDataProcessor
- retrieval_job.py: RayRetrievalJob, REMOTE_STORAGE_SCHEMES
- offline_store.py: RayOfflineStore
- utils.py: utility functions

This file re-exports them so existing imports continue to work.
"""

from .config import RayOfflineStoreConfig, RayResourceManager  # noqa: F401
from .offline_store import RayOfflineStore  # noqa: F401
from .processor import RayDataProcessor  # noqa: F401
from .retrieval_job import REMOTE_STORAGE_SCHEMES, RayRetrievalJob  # noqa: F401
from .utils import (  # noqa: F401
    _apply_to_data,
    _convert_feature_column_types,
    _get_data_schema_info,
    _handle_empty_dataframe_case,
    _safe_get_entity_timestamp_bounds,
    _safe_infer_event_timestamp_column,
    _safe_validate_schema,
)
