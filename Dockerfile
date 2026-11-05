# syntax=docker/dockerfile:1
#
# linked-archi-mcp. Streamable HTTP by default; give arguments for stdio instead.
#
#   docker run --rm -p 8000:8000 -e LINKED_ARCHI_MCP_TOKEN=... \
#     -e LINKED_ARCHI_DATA=/data/merged.trig -v "$PWD:/data:ro" ghcr.io/linked-archi/linked-archi-mcp
#   docker run -i --rm -v "$PWD:/data:ro" ghcr.io/linked-archi/linked-archi-mcp --data /data/merged.trig
#
# Build from a checkout with its submodule: the skills are bundled from vendor/linked-archi-apm.

FROM python:3.13-slim AS build
WORKDIR /src
RUN pip install --no-cache-dir --root-user-action=ignore build
COPY . .
RUN test -f vendor/linked-archi-apm/skills/linked-archi-query/SKILL.md \
      || { echo "vendor/linked-archi-apm is empty: run 'git submodule update --init'" >&2; exit 1; } \
 && python -m build --wheel --outdir /dist

FROM python:3.13-slim
LABEL io.modelcontextprotocol.server.name="io.github.linked-archi/linked-archi-mcp" \
      org.opencontainers.image.title="linked-archi-mcp" \
      org.opencontainers.image.description="Read-only MCP server for Linked.Archi architecture knowledge graphs" \
      org.opencontainers.image.source="https://github.com/linked-archi/linked-archi-mcp" \
      org.opencontainers.image.licenses="Apache-2.0"
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
COPY --from=build /dist/ /tmp/dist/
RUN pip install --no-cache-dir --root-user-action=ignore "$(ls /tmp/dist/*.whl)[shacl]" \
 && rm -rf /tmp/dist \
 && useradd --create-home --uid 10001 --shell /usr/sbin/nologin mcp
USER mcp
WORKDIR /home/mcp
EXPOSE 8000
# Binding 0.0.0.0 requires $LINKED_ARCHI_MCP_TOKEN (or --insecure-no-auth behind a gateway
# that authenticates). /healthz answers without the token.
ENTRYPOINT ["linked-archi-mcp"]
CMD ["http", "--host", "0.0.0.0"]
