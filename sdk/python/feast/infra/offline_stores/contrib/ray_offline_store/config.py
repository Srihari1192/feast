import logging
from typing import Any, Dict, List, Literal, Optional

import ray
from ray.data.context import DatasetContext

from feast.repo_config import FeastConfigBaseModel

logger = logging.getLogger(__name__)


class RayOfflineStoreConfig(FeastConfigBaseModel):
    """
    Configuration for the Ray Offline Store.

    For detailed configuration options and examples, see the documentation:
    https://docs.feast.dev/reference/offline-stores/ray
    """

    type: Literal[
        "feast.offline_stores.contrib.ray_offline_store.ray.RayOfflineStore", "ray"
    ] = "ray"
    storage_path: Optional[str] = None
    ray_address: Optional[str] = None

    # Optimization settings
    broadcast_join_threshold_mb: Optional[int] = 100
    enable_distributed_joins: Optional[bool] = True
    max_parallelism_multiplier: Optional[int] = 2
    target_partition_size_mb: Optional[int] = 64
    window_size_for_joins: Optional[str] = "1H"

    # Logging settings
    enable_ray_logging: Optional[bool] = False

    # Ray configuration for resource management (memory, CPU limits)
    ray_conf: Optional[Dict[str, Any]] = None

    # KubeRay/CodeFlare SDK configurations
    use_kuberay: Optional[bool] = None
    """Whether to use KubeRay/CodeFlare SDK for Ray cluster management"""

    cluster_name: Optional[str] = None
    """Name of the KubeRay cluster to connect to (required for KubeRay mode)"""

    auth_token: Optional[str] = None
    """Authentication token for Ray cluster connection (for secure clusters)"""

    kuberay_conf: Optional[Dict[str, Any]] = None
    """KubeRay/CodeFlare configuration parameters (passed to CodeFlare SDK)"""


class RayResourceManager:
    """
    Manages Ray cluster resources for optimal performance.
    # See: https://docs.feast.dev/reference/offline-stores/ray#resource-management-and-testing
    """

    def __init__(self, config: Optional[RayOfflineStoreConfig] = None) -> None:
        """
        Initialize the resource manager with cluster resource information.
        """
        self.config = config or RayOfflineStoreConfig()

        if not ray.is_initialized():
            self.cluster_resources = {"CPU": 4, "memory": 8 * 1024**3}
            self.available_memory = 8 * 1024**3
            self.available_cpus = 4
            self.num_nodes = 1
            return

        self.cluster_resources = ray.cluster_resources()
        self.available_memory = self.cluster_resources.get("memory", 8 * 1024**3)
        self.available_cpus = int(self.cluster_resources.get("CPU", 4))
        self.num_nodes = len(ray.nodes())

    def configure_ray_context(self) -> None:
        """
        Configure Ray DatasetContext for optimal performance based on available resources.
        """
        ctx = DatasetContext.get_current()

        if self.available_memory > 32 * 1024**3:
            ctx.target_shuffle_buffer_size = 2 * 1024**3
            ctx.target_max_block_size = 512 * 1024**2
        else:
            ctx.target_shuffle_buffer_size = 512 * 1024**2
            ctx.target_max_block_size = 128 * 1024**2

        ctx.read_op_min_num_blocks = self.available_cpus
        multiplier = (
            self.config.max_parallelism_multiplier
            if self.config.max_parallelism_multiplier is not None
            else 2
        )
        ctx.max_parallelism = self.available_cpus * multiplier
        ctx.shuffle_strategy = "sort"  # type: ignore
        ctx.enable_tensor_extension_casting = False

        if not getattr(self.config, "enable_ray_logging", False):
            ctx.enable_progress_bars = False
            if hasattr(ctx, "verbose_progress"):
                ctx.verbose_progress = False

        if getattr(self.config, "enable_ray_logging", False):
            logger.info(
                f"Configured Ray context: {self.available_cpus} CPUs, "
                f"{self.available_memory // 1024**3}GB memory, {self.num_nodes} nodes"
            )

    def estimate_optimal_partitions(self, dataset_size_bytes: int) -> int:
        """
        Estimate optimal number of partitions for a dataset based on size and resources.
        """
        target_partition_size = (self.config.target_partition_size_mb or 64) * 1024**2
        size_based_partitions = max(1, dataset_size_bytes // target_partition_size)
        max_partitions = self.available_cpus * (
            self.config.max_parallelism_multiplier or 2
        )
        return min(size_based_partitions, max_partitions)

    def should_use_broadcast_join(
        self, dataset_size_bytes: int, threshold_mb: Optional[int] = None
    ) -> bool:
        """
        Determine if dataset is small enough for broadcast join.
        """
        threshold = (
            threshold_mb
            if threshold_mb is not None
            else (self.config.broadcast_join_threshold_mb or 100)
        )
        return dataset_size_bytes <= threshold * 1024**2

    def estimate_processing_requirements(
        self, dataset_size_bytes: int, operation_type: str
    ) -> Dict[str, Any]:
        """
        Estimate resource requirements for different operations.
        """
        memory_multiplier = {
            "read": 1.2,  # 20% overhead for reading
            "join": 3.0,  # 3x for join operations
            "aggregate": 2.0,  # 2x for aggregations
            "shuffle": 2.5,  # 2.5x for shuffling
        }
        required_memory = dataset_size_bytes * memory_multiplier.get(
            operation_type, 2.0
        )
        return {
            "required_memory": required_memory,
            "optimal_partitions": self.estimate_optimal_partitions(dataset_size_bytes),
            "can_fit_in_memory": required_memory <= self.available_memory * 0.8,
            "should_broadcast": self.should_use_broadcast_join(dataset_size_bytes),
        }
