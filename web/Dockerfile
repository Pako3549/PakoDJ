FROM node:22-alpine

WORKDIR /app
COPY package.json ./
# Dependencies install fast-path for dev; production build comes with CI later.
RUN npm install && npm cache clean --force
COPY . .
CMD ["npm", "run", "dev"]
