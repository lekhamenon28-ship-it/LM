FROM node:22-alpine
WORKDIR /app
COPY common ./common
COPY care/index.js ./care/index.js
USER node
CMD ["node", "care/index.js"]
