# Keep PostgreSQL on the existing official 16 Alpine baseline; add only the
# standard-library interpreter used by the read-only backup supervisor.
FROM postgres:16-alpine
RUN apk add --no-cache python3 \
    && python3 -c 'import sys; assert sys.version_info >= (3, 11)'
