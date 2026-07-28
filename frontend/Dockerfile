FROM node:24.14.0-bookworm-slim AS build

ENV npm_config_update_notifier=false

WORKDIR /build/frontend
COPY package.json package-lock.json ./
RUN npm ci

COPY . ./
RUN npm run build


FROM scratch AS artifact

COPY --from=build /build/frontend/dist /dist
