FROM node:22-alpine@sha256:c610fcdfb1d5b4740dd70c284ed3cb16bb857e0f7166196e36a5501df7a3aa32 AS build
WORKDIR /app
COPY package.json pnpm-lock.yaml ./
RUN corepack enable && pnpm install --frozen-lockfile
COPY . .
RUN pnpm run build
RUN pnpm run build:warehouse
ARG RELEASE_PROFILE=production
RUN case "$RELEASE_PROFILE" in \
      production) node build/verify-release.mjs ;; \
      pilot) node build/verify-pilot-release.mjs ;; \
      *) echo "unsupported RELEASE_PROFILE: $RELEASE_PROFILE" >&2; exit 1 ;; \
    esac

FROM caddy:2.10-alpine@sha256:4c6e91c6ed0e2fa03efd5b44747b625fec79bc9cd06ac5235a779726618e530d
COPY --from=build /app/dist /srv/public
COPY --from=build /app/dist-warehouse /srv/warehouse
COPY Caddyfile /etc/caddy/Caddyfile
