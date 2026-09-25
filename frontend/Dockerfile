FROM node:24.14.0-bookworm-slim AS build
ARG VITE_BIZORCH_DEMO_MODE=false
ARG VITE_BIZORCH_DEMO_PASSWORD=
ENV VITE_BIZORCH_DEMO_MODE=${VITE_BIZORCH_DEMO_MODE}
ENV VITE_BIZORCH_DEMO_PASSWORD=${VITE_BIZORCH_DEMO_PASSWORD}

ENV npm_config_update_notifier=false

WORKDIR /build/frontend
COPY package.json package-lock.json ./
RUN npm ci

COPY . ./
RUN npm run build


FROM scratch AS artifact

COPY --from=build /build/frontend/dist /dist
