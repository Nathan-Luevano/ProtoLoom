FROM python:3.11-slim-bookworm AS jadx

ARG JADX_VERSION=1.5.1
ARG JADX_SHA256=12fd966431903b8e15c36e5007f19343475be7d8f2a55f082e7a929eeabc937e

RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates curl unzip \
    && curl --fail --location --retry 3 \
      --output /tmp/jadx.zip \
      "https://github.com/skylot/jadx/releases/download/v${JADX_VERSION}/jadx-${JADX_VERSION}.zip" \
    && echo "${JADX_SHA256}  /tmp/jadx.zip" | sha256sum --check --strict \
    && mkdir /opt/jadx \
    && unzip -q /tmp/jadx.zip -d /opt/jadx

FROM python:3.11-slim-bookworm

ARG VERSION=0.1.0
LABEL org.opencontainers.image.title="PROTOLOOM" \
      org.opencontainers.image.description="Recover protobuf schemas from stripped binaries" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.licenses="Apache-2.0"

RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
      default-jre-headless \
      protobuf-compiler \
    && apt-get clean \
    && find /var/lib/apt/lists -mindepth 1 -delete \
    && groupadd --system --gid 65532 protoloom \
    && useradd --system --uid 65532 --gid protoloom --create-home protoloom

COPY --from=jadx /opt/jadx /opt/jadx
COPY . /build/protoloom
RUN python -m pip install --no-cache-dir /build/protoloom \
    && rm -r /build/protoloom \
    && ln -s /opt/jadx/bin/jadx /usr/local/bin/jadx

USER protoloom
WORKDIR /work
ENTRYPOINT ["protoloom"]
CMD ["--help"]
