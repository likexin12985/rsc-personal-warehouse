FROM node:22-alpine AS build
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

FROM caddy:2.10-alpine
COPY --from=build /app/dist /srv/public
COPY --from=build /app/dist-warehouse /srv/warehouse
COPY Caddyfile /etc/caddy/Caddyfile
