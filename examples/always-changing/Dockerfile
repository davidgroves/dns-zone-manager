FROM debian:bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends dnsutils curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY churn.sh /churn.sh
RUN chmod +x /churn.sh

CMD ["/churn.sh"]
