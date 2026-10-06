# Jubran website (production image).
#   docker compose --profile app up -d --build        (from the project root)
# NEXT_PUBLIC_* values are fixed when the image is built, so pass them as build arguments.
FROM node:22-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

FROM node:22-alpine AS build
WORKDIR /app
# The API address the browser uses, e.g. https://api.example.com/api/v1 (must be https on an https site).
ARG NEXT_PUBLIC_API_URL=""
# The address printed inside table QR codes, e.g. https://order.example.com
ARG NEXT_PUBLIC_SITE_URL=""
# "true" for a copy anyone can try: "Try as a guest" on the sign-in page (with PUBLIC_DEMO=true on the API).
ARG NEXT_PUBLIC_PUBLIC_DEMO=""
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL \
    NEXT_PUBLIC_SITE_URL=$NEXT_PUBLIC_SITE_URL \
    NEXT_PUBLIC_PUBLIC_DEMO=$NEXT_PUBLIC_PUBLIC_DEMO \
    NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build && npm prune --omit=dev

FROM node:22-alpine AS run
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=3000
COPY --from=build --chown=node:node /app/package.json ./
COPY --from=build --chown=node:node /app/node_modules ./node_modules
COPY --from=build --chown=node:node /app/public ./public
COPY --from=build --chown=node:node /app/.next ./.next
COPY --from=build --chown=node:node /app/next.config.ts ./
USER node
EXPOSE 3000
CMD ["npx", "next", "start", "-p", "3000"]
