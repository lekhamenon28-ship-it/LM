FROM node:22-alpine
WORKDIR /app
COPY common ./common
COPY providers/index.js ./providers/index.js
USER node
CMD ["node", "providers/index.js"]
