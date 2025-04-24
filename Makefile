build-wheel:
	pixi run build

doc:
	pixi run -e dev --no-lockfile-update docs

test: unit integration

unit:
	pixi run -e dev --no-lockfile-update test

integration:
	pixi run -e dev --no-lockfile-update integration
