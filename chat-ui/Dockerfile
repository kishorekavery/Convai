# ---------------------------------------------------------------
# Stage 1: build the static bundle
# ---------------------------------------------------------------
FROM node:22-alpine AS builder
WORKDIR /build

# Copy manifests first so dependency installation is cached independently of
# source changes.
COPY package.json package-lock.json* ./
RUN npm install

COPY tsconfig.json vite.config.ts index.html ./
COPY src ./src
RUN npm run build

# ---------------------------------------------------------------
# Stage 2: serve
# ---------------------------------------------------------------
FROM nginx:1.27-alpine

# A template, not a finished config: the nginx image runs envsubst over
# /etc/nginx/templates at start-up, so API_UPSTREAM is resolved at run time and
# the same image works whether the API is a sibling container, on the host, or
# on another machine.
COPY templates/default.conf.template /etc/nginx/templates/default.conf.template
COPY --from=builder /build/dist /usr/share/nginx/html

# Shipped as a default; docker-compose mounts over it so the file can be
# edited on the host without rebuilding.
COPY public/config.json /usr/share/nginx/html/config.json

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
  CMD wget -qO- http://localhost/ >/dev/null 2>&1 || exit 1
