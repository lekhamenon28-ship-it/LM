FROM node:22-alpine
WORKDIR /app
COPY common ./common
COPY member/index.js ./member/index.js
USER node
CMD ["node", "member/index.js"]
