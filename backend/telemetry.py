"""
VectorBox Observability — OpenTelemetry Setup
Centralizes tracer configuration so every service can call get_tracer().
"""
import os
import logging

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.resources import Resource, SERVICE_NAME
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

logger = logging.getLogger(__name__)

_tracer_provider: TracerProvider | None = None


def setup_telemetry() -> None:
    """
    Initialize the global OTel TracerProvider with an OTLP/gRPC exporter.
    Call once at application startup (lifespan).
    Falls back gracefully if Jaeger is unreachable.
    """
    global _tracer_provider

    service_name = os.getenv("OTEL_SERVICE_NAME", "vectorbox-backend")
    otlp_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")

    resource = Resource.create({SERVICE_NAME: service_name})

    try:
        exporter = OTLPSpanExporter(endpoint=otlp_endpoint, insecure=True)
        provider = TracerProvider(resource=resource)
        provider.add_span_processor(BatchSpanProcessor(exporter))

        trace.set_tracer_provider(provider)
        _tracer_provider = provider

        logger.info(
            f"[OTel] Tracer initialized — service='{service_name}' endpoint='{otlp_endpoint}'"
        )
    except Exception as e:
        # Non-fatal: app still runs without tracing
        logger.warning(f"[OTel] Failed to initialize tracer (Jaeger unreachable?): {e}")


def get_tracer(name: str) -> trace.Tracer:
    """
    Returns a named tracer. Safe to call even if setup_telemetry() was never called
    (returns a no-op tracer in that case).
    """
    return trace.get_tracer(name)
