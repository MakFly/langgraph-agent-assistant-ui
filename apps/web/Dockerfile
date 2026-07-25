# Image de dev : le serveur Vite tourne dans le conteneur, le code est monté en
# volume, le HMR passe par le port publié.
FROM oven/bun:1.3-alpine

WORKDIR /app

# Couche de dépendances séparée du code source.
COPY package.json bun.lock ./
RUN bun install --frozen-lockfile

COPY . .

EXPOSE 4311

CMD ["bun", "run", "dev"]
