# Contributing to Feast

Thank you for your interest in contributing to Feast! This guide covers everything you need to get started — from setting up your development environment to building, testing, debugging, and submitting your changes.

For detailed, provider-specific instructions, see the full [Development Guide](docs/project/development-guide.md).

## Getting Started

### Prerequisites

- **Python** 3.10+
- **[uv](https://docs.astral.sh/uv/getting-started/installation/)** for managing Python dependencies
- **Docker** (with BuildKit) for provisioning service dependencies during testing
- **make** for running build and test scripts
- (Optional) Node & Yarn for building the Feast UI

### Install and Setup

1. Fork the repository and clone your fork:

   ```sh
   git clone https://github.com/<your-username>/feast.git
   cd feast
   ```

2. Create and activate a virtual environment:

   ```sh
   uv venv --python 3.11
   source venv/bin/activate
   ```

3. Install development dependencies:

   ```sh
   make install-python-dependencies-dev
   ```

4. Install pre-commit hooks for automatic linting and formatting:

   ```sh
   make install-precommit
   ```

## Build

### Build Python Protos

```sh
make compile-protos-python
```

### Build the Feast UI

```sh
make build-ui
```

To build and test with a local UI package:

```sh
make build-ui-local
```

### Build a Docker Image

```sh
docker build -t feast-dev -f ./sdk/python/feast/infra/feature_servers/multicloud/Dockerfile .
```

## Test

### Run Unit Tests

```sh
make test-python-unit
```

> **Note:** Ensure no local AWS configuration or Feast config overrides (`~/.feast/config`) are present, as they can interfere with unit tests.

### Run Local Integration Tests

These tests use Docker to emulate Datastore, DynamoDB, and Redis:

```sh
make test-python-integration-local
```

### Run Containerized Integration Tests

Test against emulated cloud services (Datastore, DynamoDB, Redis, Trino, HBase, Postgres, Cassandra):

```sh
make test-python-integration-container
```

### Run Provider-Specific Tests

Filter tests by provider using pytest:

```sh
python -m pytest -n 8 --integration -k Redshift sdk/python/tests
```

See the [Development Guide](docs/project/development-guide.md#integration-tests) for full cloud integration test setup (GCP, AWS, Snowflake).

## Debug

- **Unit test failures:** Run individual tests with verbose output for detailed tracebacks:

  ```sh
  python -m pytest -svx sdk/python/tests/unit/test_<module>.py
  ```

- **Integration test failures:** Check Docker containers are running and ports are available. Use `docker ps` and `docker logs <container>` to inspect service state.

- **Linting errors:** Run the formatter and linter separately to isolate issues:

  ```sh
  make format-python
  make lint-python
  ```

- **Proto compilation issues:** Ensure protobuf dependencies are installed and re-run `make compile-protos-python`.

## Code Style and Linting

Feast follows [Black](https://black.readthedocs.io/) code style with type annotations enforced by `mypy`, and linting via `ruff`.

```sh
# Auto-format code
make format-python

# Lint code
make lint-python
```

Pre-commit hooks run these automatically on each commit.

## Making a Pull Request

1. Create your changes in a **forked repo** (not a branch on the main Feast repo).
2. **Sign your commits** using `git commit -s -m "Your message"`.
3. **Rebase from master** instead of merging: `git pull -r`.
4. Ensure the PR title follows semantic conventions (e.g., `feat:`, `fix:`, `docs:`, `chore:`).
5. Add a GitHub **label** (e.g., `kind/bug`, `kind/feature`).
6. Include a release note for user-facing changes (or write `NONE` if not applicable).
7. Run tests locally before submitting.

### Pull Request Checklist

- [ ] Code follows Feast style guidelines (`make format-python && make lint-python`)
- [ ] Unit tests pass (`make test-python-unit`)
- [ ] Integration tests pass locally (`make test-python-integration-local`)
- [ ] Commits are signed off (`-s` flag)
- [ ] PR title follows semantic conventions
- [ ] Documentation updated if applicable

## Community

- **Slack:** [Feast Slack](https://slack.feast.dev/)
- **GitHub Issues:** [feast-dev/feast/issues](https://github.com/feast-dev/feast/issues)
- See the full [Community page](docs/community.md) for more ways to get involved.
