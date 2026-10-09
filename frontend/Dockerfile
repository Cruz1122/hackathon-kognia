FROM node:24.20.0 AS build-stage

ARG PUBLIC_API_URL=http://localhost:18474
ENV PUBLIC_API_URL=${PUBLIC_API_URL}

WORKDIR /usr/src/app

COPY . .

RUN npm install -g pnpm@11.3.0

RUN pnpm ci

RUN pnpm run build

FROM nginx:1.31.5-alpine

COPY --from=build-stage /usr/src/app/dist /usr/share/nginx/html
COPY nginx/default.conf /etc/nginx/conf.d/default.conf
