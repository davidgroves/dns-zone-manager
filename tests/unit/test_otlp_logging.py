"""Unit tests for OTLP logging configuration."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

from dns_zone_manager.logging import configure_logging


def test_configure_logging_without_otlp_leaves_stdout_only() -> None:
    configure_logging(log_format="json", log_level="INFO", otlp_endpoint=None)
    root = logging.getLogger()
    assert any(isinstance(h, logging.StreamHandler) for h in root.handlers)


def test_configure_logging_otlp_attaches_handler_when_sdk_present() -> None:
    fake_handler = MagicMock(spec=logging.Handler)
    fake_handler.level = logging.INFO

    with (
        patch("opentelemetry._logs.set_logger_provider") as set_provider,
        patch("opentelemetry.exporter.otlp.proto.http._log_exporter.OTLPLogExporter"),
        patch("opentelemetry.sdk._logs.LoggerProvider") as logger_provider_cls,
        patch("opentelemetry.sdk._logs.LoggingHandler", return_value=fake_handler),
        patch("opentelemetry.sdk._logs.export.BatchLogRecordProcessor"),
        patch("opentelemetry.sdk.resources.Resource") as resource_cls,
    ):
        resource_cls.create.return_value = MagicMock()
        provider = MagicMock()
        logger_provider_cls.return_value = provider

        configure_logging(
            log_format="json",
            log_level="INFO",
            otlp_endpoint="http://lgtm:4318",
        )

        set_provider.assert_called_once()
        assert fake_handler in logging.getLogger().handlers


def test_configure_logging_otlp_missing_sdk_is_soft_failure() -> None:
    # Force ImportError path inside _configure_otlp_logging
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):  # type: ignore[no-untyped-def]
        if name.startswith("opentelemetry"):
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=fake_import):
        configure_logging(
            log_format="json",
            log_level="INFO",
            otlp_endpoint="http://lgtm:4318",
        )
    # Still has stdout handler; must not raise
    assert any(isinstance(h, logging.StreamHandler) for h in logging.getLogger().handlers)
