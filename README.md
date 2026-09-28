# rollup-py

Rollup-style dependency bundling for [Hatchling](https://hatch.pypa.io/latest/), built for
[uv](https://docs.astral.sh/uv/) projects and workspaces.

`rollup-py` adds a `rollup` build target that produces a wheel containing your project **and** its
runtime dependencies (PyPI packages and uv workspace members), under a distribution name of your
choice. Vendored packages keep their import names and sit at the root of the wheel, so no import is
rewritten. The standard `sdist` and `wheel` targets (what `uv build` and `uv sync` use) are left
exactly as they are.

## Usage

```toml
# packages/app/pyproject.toml
[tool.hatch.build.targets.rollup]
distribution-name = "app-bundled"
external = ["certifi"]
```

```sh
uv lock
uvx rollup-py build --package app         # from anywhere in the workspace
# -> dist/app_bundled-0.1.0-cp312-cp312-manylinux_2_17_x86_64.manylinux2014_x86_64.whl
```

Other ways to run the same target:

```sh
uv run --with rollup-py rollup-py build packages/app
hatch build -t rollup                     # needs "rollup-py" in [build-system].requires
```

`rollup-py build [PATH] [--package NAME] [-o DIR] [--python-version V] [--python-platform P] [-c]`

| Flag | Meaning |
|---|---|
| `PATH` | Project directory (default: current directory). |
| `--package` | Workspace member to build, looked up in `uv.lock`. |
| `-o`, `--out-dir` | Output directory (default: `dist/` of the workspace root with `--package`, else of the project). |
| `--python-version`, `--python-platform` | Target environment, passed to `uv pip install` (e.g. `3.13`, `x86_64-pc-windows-msvc`). Defaults to the interpreter running `rollup-py`; pick it with e.g. `uvx --python 3.12 rollup-py build`. |
| `-c`, `--clean` | Remove earlier bundles (only files named after `distribution-name`) from the output directory. |

## Options

All options live in `[tool.hatch.build.targets.rollup]`. File selection options of
`[tool.hatch.build.targets.wheel]` (`packages`, `only-include`, `sources`, `exclude`, hooks, ...) are
inherited and can be overridden there.

| Option | Default | Meaning |
|---|---|---|
| `distribution-name` | `"{name}-rollup"` | Name of the bundled distribution. Must differ from the project name. |
| `vendor` | `["*"]` | Direct dependencies to bundle; `"*"` means all of them. |
| `external` | `[]` | Packages never bundled. They become `Requires-Dist` entries of the bundle. |
| `extras`, `groups` | `[]` | Extras (`[project.optional-dependencies]`) and dependency groups (`[dependency-groups]`) of the project whose dependencies are bundled like `dependencies`, as `uv sync --extra`/`--group` would install them. Those that are not bundled become unconditional requirements. |
| `transitive` | `true` | Also bundle the dependencies of bundled packages. When `false` they become requirements. |
| `conditional` | `"external"` | Dependencies behind an environment marker: `"external"` keeps them as requirements with their marker; `"evaluate"` evaluates the marker for the target environment and bundles or drops them. |
| `lock` | `"auto"` | Path to `uv.lock`; `"auto"` searches upwards from the project. |
| `check-lock` | `true` | Run `uv lock --check` first and fail on a stale lock. |
| `python-version`, `python-platform` | | Same as the CLI flags (the flags win). |

### Different dependencies for development and the bundle

`extras` and `groups` pick a set of dependencies that `uv sync` leaves out, e.g. a patched fork that
only the bundle should ship, while development uses the original:

```toml
[project.optional-dependencies]
patched = ["six"]

[dependency-groups]
dev = ["six"]

[tool.uv]
conflicts = [[{ extra = "patched" }, { group = "dev" }]]

[tool.uv.sources]
six = [{ git = "https://github.com/me/six", branch = "fix", extra = "patched" }]

[tool.hatch.build.targets.rollup]
extras = ["patched"]
```

With `[tool.uv] conflicts`, `uv.lock` holds both versions and marks the edges of other packages
with the extra or group they belong to (`extra == 'extra-3-app-patched'`). The bundle follows the
selected ones, so a package that depends on `six` gets the fork too. Selecting two conflicting items
is an error.

## How it works

1. `uv.lock` is read to find the project, walk its dependency graph (from `dependencies` plus the
   selected `extras` and `groups`) and split it into bundled and external packages. A walk stops at
   every external package, since the installer handles its dependencies.
2. Each bundled package is installed at its locked version with
   `uv pip install --target <tmp> --no-deps`: registry and URL packages with `--require-hashes` and
   every hash from the lock, git packages at their locked commit, workspace members from their
   directory.
3. Files listed in each installed `RECORD` are added to the wheel root. Scripts and data files
   (`bin/`, `share/`, ...) are left out. Two packages writing the same path, or a package clashing
   with the project's own files, is an error; namespace packages merge.
4. The wheel tag is the loosest tag every bundled wheel is compatible with: e.g. `cp312-cp312` and
   `cp38-abi3` give `cp312-cp312`, and `manylinux_2_17` and `manylinux_2_28` give `manylinux_2_28`.
   A bundle of pure-Python packages stays `py3-none-any`.
5. `METADATA` gets the new `Name`, loses the `Requires-Dist` entries of bundled packages (including
   in extras) and gains the requirements that bundled packages have on external ones.
   `<dist-info>/rollup.json` lists what was bundled and `<dist-info>/vendor/` keeps each bundled
   package's `METADATA` and license files.

The build fails if an external package (or one of your extras) depends on a bundled package,
because installing both would put two copies of the same files in `site-packages`.

## Limitations

- Bundled packages have no `.dist-info` of their own (a wheel can only carry one), so
  `importlib.metadata.version("requests")` and similar lookups fail. The build warns when bundled
  code uses `importlib.metadata` or `pkg_resources`.
- Installing the bundle next to a separately installed copy of a bundled package, or next to the
  project's standard wheel, makes the two distributions share files.
- `uv build` cannot build this target: hatchling's PEP 517 entry points only build `sdist` and
  `wheel`. Use `rollup-py build` or `hatch build -t rollup`.
- Nothing is tree-shaken; packages are bundled whole.
- Only the project's own `extras` and `groups` count for `[tool.uv] conflicts`; conflicting extras
  of other packages are resolved as if none were enabled.

## Development

```sh
uv sync
uv run ruff check && uv run ruff format --check && uv run pyright
uv run pytest -m "not e2e"   # unit tests
uv run pytest -m e2e         # builds tests/fixtures/ws for real; needs access to PyPI
```
