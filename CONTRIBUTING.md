# Contributing to Feast

Thank you for your interest in contributing to Feast! This guide covers the essentials to get you started. For the complete development guide, see [docs/project/development-guide.md](docs/project/development-guide.md).

## Architecture

Before diving in, review the project's architecture to understand how components fit together:

- [ARCHITECTURE.md](ARCHITECTURE.md) — system overview, core abstractions, data flow, and extension points
- [Architecture Decision Records](docs/adr/README.md) — key design decisions and their rationale

## Setup

### Prerequisites

- Python 3.9+
- Go 1.22+ (for the operator)
- Make
- Git

### Install Development Dependencies

```bash
# Clone your fork
git clone https://github.com/<your-username>/feast.git
cd feast

# Install Python SDK in development mode
make install-python-dependencies-dev

# Or install minimal dependencies
make install-python-dependencies-minimal
```

## Build

```bash
# Build Python protobuf files
make compile-protos-python

# Build Go code
make build-go

# Build all Docker images
make build-docker

# Build the feature server image
make build-feature-server-docker
```

## Test

```bash
# Run Python unit tests
make test-python-unit

# Run Python unit tests (fast subset)
make test-python-unit-fast

# Run local integration tests
make test-python-integration-local

# Run Go tests
make test-go

# Run Java tests
make test-java
```

See the [full development guide](docs/project/development-guide.md#unit-tests) for integration test setup and provider-specific test instructions.

## Lint and Format

```bash
# Install pre-commit hooks (recommended)
pre-commit install

# Format Python code
make format-python

# Lint Python code
make lint-python

# Type check Python
cd sdk/python && python -m mypy feast

# Format and lint Go code
make format-go
make lint-go
```

## Debug

- **Python SDK**: Use `FEAST_LOG_LEVEL=DEBUG` environment variable to enable verbose logging
- **Feature Server**: Run `feast serve --log-level debug` for detailed request/response logging
- **Local testing**: Use `feast plan` to preview changes before `feast apply`
- **Protobuf issues**: Recompile with `make compile-protos-python` after changing `.proto` files

## Making a Pull Request

1. Fork the repository and create a feature branch
2. Follow the [PR checklist](docs/project/development-guide.md#pull-request-checklist)
3. Use [conventional commit](https://www.conventionalcommits.org/) messages (e.g., `feat:`, `fix:`, `docs:`)
4. Sign your commits (`git commit -s`)
5. Run tests locally before submitting
6. Install [pre-commit hooks](docs/project/development-guide.md#pre-commit-hooks) for automated formatting

For the complete contribution process, including maintainer instructions, see the [full Development Guide](docs/project/development-guide.md).
