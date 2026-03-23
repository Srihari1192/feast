FROM redis:latest

# Feast integration test Redis image
# Multi-arch: amd64, arm64, ppc64le, s390x
# Runs Redis without authentication on port 6379

EXPOSE 6379

CMD ["redis-server", "--protected-mode", "no"]
