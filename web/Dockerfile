# PakoDJ webapp image.
# Target "dev"  : Vite dev server with hot reload (root compose.yml).
# Target "prod" : static build served by an internal nginx (deploy composes
#                 + GHCR image). The outer nginx still fronts it so the
#                 platform keeps a single entry point.
# syntax=docker/dockerfile:1
FROM node:22-alpine AS dev
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund && npm cache clean --force
COPY . .
EXPOSE 3000
CMD ["npm", "run", "dev"]

FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
RUN npm run build

FROM nginx:stable-alpine AS prod
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
