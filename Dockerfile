# TensorFork in a container: an offline Bittensor mainnet fork on port 9944.
#
#   docker build -t tensorfork .
#   docker run --rm -p 127.0.0.1:9944:9944 -v ~/.tensorfork/snapshots:/data/snapshots tensorfork
#   # no snapshot yet? one-off download into the same volume:
#   docker run --rm -v ~/.tensorfork/snapshots:/data/snapshots tensorfork snapshot pull
#
# Inside the container the proxy listens on 0.0.0.0 so the port can be published;
# publish it on 127.0.0.1 as above. The engine and upstream stay on loopback.
FROM python:3.12-slim

ENV TENSORFORK_HOME=/opt/tensorfork \
    PIP_NO_CACHE_DIR=1 \
    PYTHONUNBUFFERED=1

WORKDIR /src
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN pip install . \
 && tensorfork setup \
 && mkdir -p /data/snapshots \
 && ln -s /data/snapshots "$TENSORFORK_HOME/snapshots"

VOLUME ["/data/snapshots"]
EXPOSE 9944
ENTRYPOINT ["tensorfork"]
CMD ["up", "--foreground", "--host", "0.0.0.0"]
