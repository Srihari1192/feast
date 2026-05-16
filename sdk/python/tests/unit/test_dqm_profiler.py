# Copyright 2025 The Feast Authors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Unit tests for the DQM profiler base classes and ValidationFailed exception."""

import pytest

from feast.dqm.errors import ValidationFailed
from feast.dqm.profilers.profiler import ValidationError, ValidationReport

# ---------------------------------------------------------------------------
# Helpers – minimal concrete implementations of the abstract base classes
# ---------------------------------------------------------------------------


class _SimpleReport(ValidationReport):
    """Minimal ValidationReport that holds a fixed list of errors."""

    def __init__(self, errors):
        self._errors = errors

    @property
    def is_success(self) -> bool:
        return len(self._errors) == 0

    @property
    def errors(self):
        return self._errors


# ---------------------------------------------------------------------------
# ValidationError tests
# ---------------------------------------------------------------------------


class TestValidationError:
    def test_required_fields(self):
        err = ValidationError(
            check_name="expect_column_values_to_not_be_null", column_name="driver_id"
        )
        assert err.check_name == "expect_column_values_to_not_be_null"
        assert err.column_name == "driver_id"

    def test_optional_fields_default_to_none(self):
        err = ValidationError(check_name="check", column_name="col")
        assert err.check_config is None
        assert err.missing_count is None
        assert err.missing_percent is None
        assert err.observed_value is None
        assert err.unexpected_count is None
        assert err.unexpected_percent is None

    def test_all_fields_set(self):
        err = ValidationError(
            check_name="expect_column_mean_to_be_between",
            column_name="conv_rate",
            check_config={"min_value": 0.1, "max_value": 0.9},
            missing_count=0,
            missing_percent=0.0,
            observed_value=0.45,
            unexpected_count=3,
            unexpected_percent=1.5,
        )
        assert err.check_config == {"min_value": 0.1, "max_value": 0.9}
        assert err.observed_value == 0.45
        assert err.unexpected_percent == 1.5

    def test_to_dict_contains_all_keys(self):
        err = ValidationError(
            check_name="null_check",
            column_name="feature_a",
            missing_count=2,
            missing_percent=5.0,
        )
        d = err.to_dict()
        assert set(d.keys()) == {
            "check_name",
            "column_name",
            "check_config",
            "missing_count",
            "missing_percent",
            "observed_value",
            "unexpected_count",
            "unexpected_percent",
        }
        assert d["check_name"] == "null_check"
        assert d["column_name"] == "feature_a"
        assert d["missing_count"] == 2
        assert d["missing_percent"] == 5.0
        assert d["check_config"] is None

    def test_to_dict_roundtrip(self):
        err = ValidationError(
            check_name="range_check",
            column_name="acc_rate",
            observed_value=1.5,
            unexpected_count=10,
            unexpected_percent=2.0,
        )
        d = err.to_dict()
        assert d["observed_value"] == 1.5
        assert d["unexpected_count"] == 10
        assert d["unexpected_percent"] == 2.0

    def test_repr(self):
        err = ValidationError(check_name="my_check", column_name="my_col")
        assert "my_check" in repr(err)
        assert "my_col" in repr(err)


# ---------------------------------------------------------------------------
# ValidationReport tests (via _SimpleReport)
# ---------------------------------------------------------------------------


class TestValidationReport:
    def test_success_when_no_errors(self):
        report = _SimpleReport(errors=[])
        assert report.is_success is True
        assert report.errors == []

    def test_failure_when_errors_present(self):
        err = ValidationError(check_name="null_check", column_name="feature_x")
        report = _SimpleReport(errors=[err])
        assert report.is_success is False
        assert len(report.errors) == 1

    def test_multiple_errors(self):
        errors = [
            ValidationError(check_name=f"check_{i}", column_name=f"col_{i}")
            for i in range(5)
        ]
        report = _SimpleReport(errors=errors)
        assert report.is_success is False
        assert len(report.errors) == 5


# ---------------------------------------------------------------------------
# ValidationFailed exception tests
# ---------------------------------------------------------------------------


class TestValidationFailed:
    def test_stores_report(self):
        report = _SimpleReport(
            errors=[ValidationError(check_name="c", column_name="x")]
        )
        exc = ValidationFailed(report)
        assert exc.validation_report is report
        assert exc.report is report

    def test_is_exception(self):
        report = _SimpleReport(errors=[])
        exc = ValidationFailed(report)
        assert isinstance(exc, Exception)

    def test_can_be_raised_and_caught(self):
        report = _SimpleReport(
            errors=[ValidationError(check_name="null_check", column_name="driver_id")]
        )
        with pytest.raises(ValidationFailed) as exc_info:
            raise ValidationFailed(report)
        assert exc_info.value.report is report
        assert not exc_info.value.report.is_success

    def test_report_errors_accessible_after_catch(self):
        err = ValidationError(
            check_name="range_check",
            column_name="conv_rate",
            observed_value=2.0,
        )
        report = _SimpleReport(errors=[err])
        with pytest.raises(ValidationFailed) as exc_info:
            raise ValidationFailed(report)
        caught_errors = exc_info.value.report.errors
        assert len(caught_errors) == 1
        assert caught_errors[0].column_name == "conv_rate"
        assert caught_errors[0].observed_value == 2.0
