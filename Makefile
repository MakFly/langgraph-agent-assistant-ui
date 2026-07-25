# LangGraph POC — tout passe par docker compose.
#
# Les ports sont lus depuis docker-compose.yml pour éviter une seconde source
# de vérité. Valeurs de repli si le parsing échoue.

API_PORT ?= $(or $(shell sed -n 's/.*"\([0-9]*\):4310".*/\1/p' docker-compose.yml),4310)
WEB_PORT ?= $(or $(shell sed -n 's/.*"\([0-9]*\):4311".*/\1/p' docker-compose.yml),4311)

COMPOSE := docker compose

.DEFAULT_GOAL := help
.PHONY: help install up dev down stop restart logs logs-api logs-web test test-unit lint typecheck build check shell-api shell-web clean

help: ## Affiche cette aide
	@grep -hE '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'
	@echo ""
	@echo "  API : http://localhost:$(API_PORT)   Front : http://localhost:$(WEB_PORT)   Logs : make logs"

install: ## Prépare apps/api/.env et construit les images
	@test -f apps/api/.env || (cp apps/api/.env.example apps/api/.env && \
		echo "→ apps/api/.env créé : renseignez une clé LLM avant make dev")
	$(COMPOSE) build

dev: ## Démarre la stack en arrière-plan (hot reload actif sur les deux services)
	$(COMPOSE) up -d
	@echo "→ API    http://localhost:$(API_PORT)"
	@echo "→ Front  http://localhost:$(WEB_PORT)"
	@echo "→ Logs   make logs"

up: dev ## Alias de dev

logs: ## Suit les logs des deux services
	$(COMPOSE) logs -f

logs-api: ## Suit les logs de l'API
	$(COMPOSE) logs -f api

logs-web: ## Suit les logs du front
	$(COMPOSE) logs -f web

stop: ## Arrête les conteneurs sans les supprimer
	$(COMPOSE) stop

down: ## Arrête et supprime les conteneurs
	$(COMPOSE) down

restart: ## Redémarre la stack
	$(COMPOSE) restart

# --- Qualité -----------------------------------------------------------------
# Tout tourne dans le conteneur : aucun outil Python n'est requis sur l'hôte.

test: ## Tous les tests de l'API (réseau requis : les outils tapent leurs vraies APIs)
	$(COMPOSE) run --rm --no-deps api pytest -v

test-unit: ## Tests hors réseau uniquement (graphe, conversion, calculateur)
	$(COMPOSE) run --rm --no-deps api pytest -v --deselect tests/test_tools.py::TestApisExternes

lint: ## Ruff sur l'API
	$(COMPOSE) run --rm --no-deps api ruff check src tests

typecheck: ## Types du front
	cd apps/web && bunx tsc -b

build: ## Build de production du front
	cd apps/web && bun run build

check: test lint typecheck build ## Enchaîne tests, lint, types et build

# --- Divers ------------------------------------------------------------------

shell-api: ## Ouvre un shell dans le conteneur API
	$(COMPOSE) run --rm --no-deps api bash

shell-web: ## Ouvre un shell dans le conteneur front
	$(COMPOSE) run --rm --no-deps web sh

clean: down ## Supprime conteneurs, volumes et artefacts de build
	$(COMPOSE) down -v
	rm -rf apps/web/dist apps/api/.pytest_cache apps/api/.ruff_cache
	find . -name "__pycache__" -type d -prune -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.tsbuildinfo" -not -path "*/node_modules/*" -delete
	@echo "→ nettoyé"
