# Observability config for grafana/otel-lgtm (examples + .devcontainer).
#
# Mounted into the `lgtm` Compose service:
#   otelcol-config.yaml  — scrapes dns-zone-manager /metrics into Prometheus
#   dashboards/          — provisioned Grafana dashboard covering all metrics
#
# Logs: set logging.otlp_endpoint (or OTEL_EXPORTER_OTLP_ENDPOINT) on the API
# so structured logs are exported over OTLP HTTP → collector → Loki.
#
# Grafana UI: http://localhost:3000
