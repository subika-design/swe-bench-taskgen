.PHONY: setup check install pipeline help

REPO ?=

help:
	@echo "Targets:"
	@echo "  make setup              Install dependencies, create .env"
	@echo "  make check              Preflight (deps, tokens, gh, docker)"
	@echo "  make pipeline REPO=o/r  Run full pipeline for owner/repo"
	@echo ""
	@echo "Example: make pipeline REPO=curl/curl"

setup:
	./setup.sh

check:
	./run_pipeline.sh --check

install: setup

pipeline:
	@test -n "$(REPO)" || (echo "Usage: make pipeline REPO=owner/repo" >&2; exit 1)
	./run_pipeline.sh "$(REPO)"
