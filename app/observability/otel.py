from __future__ import annotations

from app.core.config import settings


def configure_otel(app) -> dict:
    if not settings.otel_enabled:
        return {"enabled":False}
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError as exc:
        raise RuntimeError("AIOC_OTEL_ENABLED=true but OpenTelemetry dependencies are unavailable") from exc
    provider=TracerProvider(resource=Resource.create({"service.name":settings.otel_service_name}))
    if settings.otel_endpoint:
        exporter=OTLPSpanExporter(endpoint=settings.otel_endpoint)
        provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(app)
    return {"enabled":True,"endpoint":bool(settings.otel_endpoint),"service_name":settings.otel_service_name}
