.PHONY: bump version-check

# Usage: make bump VERSION=0.1.6
bump:
	python scripts/bump_version.py $(if $(VERSION),--version $(VERSION),)

version-check:
	python scripts/bump_version.py --check
