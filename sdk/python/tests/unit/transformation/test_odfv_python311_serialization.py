"""
Regression test for feast-dev/feast#5679:
IndexError on Python 3.11 with On-Demand Feature Views.

The bug: dill.dumps(udf, recurse=True) crashes with
  IndexError: tuple index out of range
on Python 3.11 due to CACHE bytecode instructions (PEP 659).

Crash path in production:
  FeatureStore.__init__()
    → SqlRegistry.proto()
      → OnDemandFeatureView.to_proto()
        → Transformation.to_proto()          # base.py:98
          → dill.dumps(self.udf, recurse=True)
            → dill.detect.nestedglobals()
              → dis.dis(func)
                → IndexError

This test exercises the full decorator → to_proto → from_proto round-trip
to ensure ODFV UDF serialization works on all Python versions (3.10–3.12).
"""

import dill
import pandas as pd

from feast.data_source import RequestSource
from feast.field import Field
from feast.on_demand_feature_view import OnDemandFeatureView, on_demand_feature_view
from feast.types import Float64


def test_odfv_decorator_to_proto_roundtrip():
    """Full @on_demand_feature_view → to_proto → from_proto round-trip."""
    request_source = RequestSource(
        name="input_source",
        schema=[Field(name="base_feature", dtype=Float64)],
    )

    @on_demand_feature_view(
        sources=[request_source],
        schema=[Field(name="transformed_feature", dtype=Float64)],
        mode="pandas",
    )
    def feature_transformations(inputs: pd.DataFrame) -> pd.DataFrame:
        df = pd.DataFrame()
        df["transformed_feature"] = inputs["base_feature"] / 100
        return df

    # Serialize: the exact path that crashes on Python 3.11 with old dill
    proto = feature_transformations.to_proto()

    # Deserialize: the path that fails with EOFError
    restored_odfv = OnDemandFeatureView.from_proto(proto)

    # Verify the restored UDF produces correct results
    test_input = pd.DataFrame({"base_feature": [100.0, 200.0, 300.0]})
    result = restored_odfv.feature_transformation.udf(test_input)
    assert result["transformed_feature"].tolist() == [1.0, 2.0, 3.0]


def test_odfv_udf_dill_dumps_recurse():
    """dill.dumps(udf, recurse=True) must not raise IndexError on Python 3.11+."""

    def udf(inputs: pd.DataFrame) -> pd.DataFrame:
        df = pd.DataFrame()
        df["transformed"] = inputs["base_feature"] / 100
        return df

    # This is the exact call in Transformation.to_proto() (base.py:98)
    body = dill.dumps(udf, recurse=True)
    restored = dill.loads(body)

    result = restored(pd.DataFrame({"base_feature": [100, 200, 300]}))
    assert result["transformed"].tolist() == [1.0, 2.0, 3.0]


def test_odfv_udf_with_globals_and_closures():
    """UDF with globals, imports, and lambdas serializes correctly."""
    SCALE = 100

    def complex_udf(inputs: pd.DataFrame) -> pd.DataFrame:
        import numpy as np

        df = pd.DataFrame()
        df["scaled"] = inputs["value"] * SCALE
        df["log_val"] = np.log1p(inputs["value"])
        df["category"] = inputs["value"].apply(lambda x: "high" if x > 50 else "low")
        return df

    body = dill.dumps(complex_udf, recurse=True)
    restored = dill.loads(body)

    result = restored(pd.DataFrame({"value": [42.0, 100.0]}))
    assert result["scaled"].tolist() == [4200.0, 10000.0]
    assert result["category"].tolist() == ["low", "high"]
