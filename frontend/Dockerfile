# Production build of the SPA, served by nginx, which also proxies /v1 and /dev to the api service.
#   docker build -t anjung-web frontend
# src/api/schema.d.ts is committed, so the build does not need ../contracts (run `npm run gen:api` first if it changed).
FROM node:22 AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
RUN npx tsc --noEmit && npx vite build

FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
