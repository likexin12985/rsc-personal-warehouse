# Keep PostgreSQL on the existing official 16 Alpine baseline.  The target
# host's container egress cannot reliably reach Alpine's package CDN, so copy
# the interpreter from the pinned Alpine Python runtime instead of running
# apk during the production image build.  The backup supervisor only needs
# the Python standard library.
FROM python:3.12-alpine@sha256:0687a6bc9716edc2a6ee0fbfb0f87e7ee358b262b67c9215de91bc9b2d38ba71 AS python_runtime
FROM postgres:16-alpine

COPY --from=python_runtime /usr/local/bin/python3.12 /usr/local/bin/python3.12
COPY --from=python_runtime /usr/local/bin/python3.12-config /usr/local/bin/python3.12-config
COPY --from=python_runtime /usr/local/lib/libpython3.12.so.1.0 /usr/local/lib/libpython3.12.so.1.0
COPY --from=python_runtime /usr/local/lib/python3.12 /usr/local/lib/python3.12

RUN ln -sf python3.12 /usr/local/bin/python3 \
    && ln -sf python3 /usr/local/bin/python \
    && ln -sf libpython3.12.so.1.0 /usr/local/lib/libpython3.12.so \
    && python3 -c 'import sys; assert sys.version_info >= (3, 11)'
