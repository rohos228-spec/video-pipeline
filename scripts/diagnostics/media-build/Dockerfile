# Secret-free extension of the existing offline diagnostic image.
# The launcher records the resolved base and result image IDs. Runtime tests
# continue with --network none; this build only installs the approved tools.
ARG DIAGNOSTIC_BASE=vp-offline-diagnostic:20260924
FROM ${DIAGNOSTIC_BASE}
USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && ffmpeg -version \
    && ffprobe -version \
    && rm -rf /var/lib/apt/lists/*
