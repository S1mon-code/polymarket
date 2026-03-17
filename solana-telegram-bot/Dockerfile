FROM node:20-slim

# better-sqlite3 needs build tools for native bindings
RUN apt-get update && apt-get install -y python3 make g++ && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install all deps (including devDependencies for tsc build)
COPY package*.json ./
RUN npm ci

# Copy source and build
COPY . .
RUN npx tsc

# Remove devDependencies after build
RUN npm prune --production

CMD ["node", "dist/index.js"]
