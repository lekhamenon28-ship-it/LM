FROM node:22-alpine
WORKDIR /app
COPY common ./common
COPY claims/index.js ./claims/index.js
USER node
CMD ["node", "claims/index.js"]
