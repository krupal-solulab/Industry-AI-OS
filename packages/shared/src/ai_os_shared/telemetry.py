"""Observability — OpenTelemetry traces/metrics + structured logging.

`setup_telemetry(app, service_name)` is called once by the shared app factory. It
wires OTLP export to the collector and instruments FastAPI. LLM-specific tracing
(cost, tokens, prompts) is handled separately by Langfuse in the orchestrator; this
module covers generic service spans so every hop is observable end to end.
"""

from __future__ import annotations

import logging

import structlog

from ai_os_shared.settings import get_settings

_configured = False


def configure_logging(service_name: str) -> None:
    settings = get_settings()
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    logging.basicConfig(level=level, format="%(message)s")
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
    structlog.contextvars.bind_contextvars(service=service_name)


def setup_telemetry(app, service_name: str) -> None:
    """Idempotently configure logging + OTel tracing for a FastAPI app."""
    global _configured
    configure_logging(service_name)
    if _configured:
        return
    settings = get_settings()
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        resource = Resource.create(
            {"service.name": service_name, "service.namespace": settings.otel_namespace}
        )
        provider = TracerProvider(resource=resource)
        provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=settings.otel_endpoint, insecure=True))
        )
        trace.set_tracer_provider(provider)
        FastAPIInstrumentor.instrument_app(app)
        _configured = True
    except Exception as exc:  # telemetry must never crash a service on boot
        structlog.get_logger("aios.telemetry").warning("otel.setup_failed", error=str(exc))
