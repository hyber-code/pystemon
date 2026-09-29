#
# Builder stage: compile wheels (lxml) once, keep the compiler out of the final image
#
FROM python:3.12-alpine AS builder
WORKDIR /usr/src/app
COPY requirements.txt .
RUN apk add --no-cache g++ gcc libxml2-dev libxslt-dev && \
    pip wheel --no-cache-dir --wheel-dir /usr/src/app/wheels -r requirements.txt

#
# Runtime stage: small, non-root
#
FROM python:3.12-alpine
LABEL name="pystemon" \
      description="Monitoring tool for PasteBin-alike sites written in Python" \
      url="https://github.com/cvandeplas/pystemon" \
      maintainer="christophe@vandeplas.com"

RUN apk add --no-cache libxml2 libxslt && \
    adduser -D -u 10001 -h /opt/pystemon pystemon
COPY --from=builder /usr/src/app/wheels /wheels
RUN pip install --no-cache-dir --no-index --find-links=/wheels /wheels/*.whl && rm -rf /wheels

WORKDIR /opt/pystemon
COPY --chown=pystemon:pystemon . /opt/pystemon
# alerts, archive and the sqlite file are written to /data (mount a volume there)
RUN mkdir -p /data && chown pystemon:pystemon /data
USER pystemon
VOLUME ["/data"]
ENTRYPOINT ["python", "/opt/pystemon/pystemon.py"]
CMD ["-c", "/opt/pystemon/pystemon.yaml"]
