# Blackbox Jury mock tool gateway. Stdlib-only Python; no packages installed.
# Base pinned by multi-arch index digest (python:3.12-slim) for reproducibility.
FROM python:3.12-slim@sha256:a0f57064cbdcd6285fa45f51237ab0b35ea8ebaed06a1f5973e89e19dfb86704

LABEL org.opencontainers.image.title="bbj-gateway" \
      org.opencontainers.image.description="Blackbox Jury mock tool gateway (synthetic data only)" \
      bbj.component="gateway"

# Installed into site-packages (root-owned, read-only to the runtime user) so it
# imports under `python -I` (isolated mode: no cwd, no PYTHON* env vars on sys.path).
COPY bbj_gateway/ /usr/local/lib/python3.12/site-packages/bbj_gateway/
WORKDIR /tmp

# Numeric non-root user; the runner also enforces user, read-only rootfs,
# dropped capabilities, no-new-privileges, resource limits and no egress.
USER 10001:10001
EXPOSE 8080
# -I isolated mode, -B no bytecode writes, -u unbuffered output (evidence/final line).
ENTRYPOINT ["python", "-I", "-B", "-u", "-m", "bbj_gateway"]
