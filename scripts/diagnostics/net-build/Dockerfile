FROM vp-offline-diagnostic:20260924
RUN apt-get update \
    && apt-get install -y --no-install-recommends nftables \
    && rm -rf /var/lib/apt/lists/*
