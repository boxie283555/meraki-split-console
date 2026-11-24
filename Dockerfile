FROM python:3.12-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends smartdns dnsutils openssl ca-certificates tini && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY *.py backup-catalog.json RESTORE.md /app/
COPY web/ /app/web/
ENV PYTHONUNBUFFERED=1 CONTAINER_RUNTIME=1 APP_BIND=0.0.0.0 APP_DATA=/data APP_PRIVATE=/data/private APP_CONFIG=/data/config.json SMARTDNS_STATE=/data/dns SMARTDNS_ADDRESS=0.0.0.0 SMARTDNS_PORT=5353
VOLUME /data
EXPOSE 8443 5353/tcp 5353/udp
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s CMD python -c "import ssl,urllib.request; r=urllib.request.Request('https://127.0.0.1:8443/',headers={'Host':'localhost:8443'}); assert urllib.request.urlopen(r,context=ssl._create_unverified_context(),timeout=3).status==200"
ENTRYPOINT ["/usr/bin/tini","-g","--"]
CMD ["python","/app/bootstrap.py"]
