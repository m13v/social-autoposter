# Cloud Run image for the hosted client dashboard. Local operators keep
# running `node bin/server.js` directly on their Mac; this image is only
# for CLIENT_MODE=1 deployments (no launchd, no python shellouts).
FROM node:22-alpine

WORKDIR /app

ENV NODE_ENV=production \
    CLIENT_MODE=1 \
    PORT=8080

COPY package.json package-lock.json* ./
RUN npm ci --omit=dev --no-audit --no-fund

COPY bin ./bin
COPY config.example.json ./config.example.json

EXPOSE 8080
CMD ["node", "bin/server.js"]
