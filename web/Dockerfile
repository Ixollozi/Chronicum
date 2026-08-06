FROM node:22-alpine
WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .

ARG NEXT_PUBLIC_API_URL=http://localhost:8000
ARG NEXT_PUBLIC_WEB_API_TOKEN=chronicum-dev-token
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL
ENV NEXT_PUBLIC_WEB_API_TOKEN=$NEXT_PUBLIC_WEB_API_TOKEN

RUN npm run build

EXPOSE 3000
CMD ["npm", "run", "start"]
