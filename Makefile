# SPDX-FileCopyrightText: 2026 Afri Blank (@l5yth)
# SPDX-License-Identifier: Apache-2.0

# Interpreter with RNS and cbor2 available. Override for other machines:
#   make test PYTHON=/path/to/python
PYTHON ?= $(if $(RRC_PYTHON),$(RRC_PYTHON),python3)

.PHONY: help test coverage fmt fmt-check docs licence check e2e clean

help:  ## Show available targets
	@grep -hE '^[a-z0-9-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN{FS=":.*?## "}{printf "  %-12s %s\n", $$1, $$2}'

test:  ## Run the unit suite (ACCEPTANCE B1)
	$(PYTHON) -m pytest -q

coverage:  ## Enforce the 100% coverage floor (ACCEPTANCE B2)
	$(PYTHON) -m coverage run -m pytest -q
	$(PYTHON) -m coverage report -m --fail-under=100

fmt:  ## Format with black
	$(PYTHON) -m black .

fmt-check:  ## Verify formatting (ACCEPTANCE B5)
	$(PYTHON) -m black --check --diff .

docs:  ## Verify 100% API-doc coverage (ACCEPTANCE B3)
	$(PYTHON) .github/scripts/check_docstrings.py

licence:  ## Verify SPDX tags and notices on tracked files (ACCEPTANCE B4)
	@for f in $$(git ls-files); do \
		[ "$$f" = LICENSE ] && continue; \
		grep -qF 'SPDX-License-Identifier: Apache-2.0' "$$f" \
			|| echo "MISSING SPDX: $$f"; \
		case "$$f" in *.py) grep -qF 'Licensed under the Apache License, Version 2.0' \
			"$$f" || echo "MISSING NOTICE: $$f";; esac; \
	done; echo "licence headers checked"

check: fmt-check test coverage docs licence  ## Everything that gates a change

e2e:  ## End-to-end against a local rrcd hub (ACCEPTANCE A1)
	$(PYTHON) -m pytest -q tests/test_e2e.py -rs -s

clean:  ## Remove build and test artefacts
	find . -name __pycache__ -type d -exec rm -r {} + 2>/dev/null || true
	rm -f .coverage
	rm -r .pytest_cache 2>/dev/null || true
