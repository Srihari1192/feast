"""
Regression test for feast-dev/feast#5679:
IndexError on Python 3.11 with On-Demand Feature Views.

Verifies that ODFV UDF serialization via dill.dumps(recurse=True)
works correctly on Python 3.11+.
"""
import dill
import pandas as pd

from feast.transformation.base import Transformation


def test_odfv_udf_serialization_roundtrip():
    """ODFV UDF should serialize and deserialize correctly on Python 3.11+."""

    def udf(inputs: pd.DataFrame) -> pd.DataFrame:
        df = pd.DataFrame()
        df["transformed"] = inputs["base_feature"] / 100
        return df

    t = Transformation(mode="pandas", udf=udf, udf_string="udf")
    proto = t.to_proto()

    restored = dill.loads(proto.body)
    result = restored(pd.DataFrame({"base_feature": [100, 200, 300]}))
    assert result["transformed"].tolist() == [1.0, 2.0, 3.0]
