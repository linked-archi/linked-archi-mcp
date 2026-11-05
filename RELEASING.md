# Releasing

Pushing a tag `vX.Y.Z` runs [`.github/workflows/release.yml`](.github/workflows/release.yml):

1. **build** checks before anything is published:
   - the tag matches `__version__`;
   - the CHANGELOG section is written and dated;
   - `server.json` validates against the registry schema it declares;
   - the sdist and wheel build and pass `twine check`;
   - the wheel works from a clean install, outside the checkout.
2. **pypi** uploads the sdist and wheel with trusted publishing. No token is stored.
3. **registry** waits until PyPI serves the version, then publishes `server.json` to the
   [MCP Registry](https://registry.modelcontextprotocol.io) as
   `io.github.linked-archi/linked-archi-mcp`. It authenticates with GitHub OIDC.
4. **image** builds `linux/amd64` and `linux/arm64` images and pushes
   `ghcr.io/linked-archi/linked-archi-mcp:X.Y.Z`, `:X.Y` and `:latest`.
5. **github-release** creates the GitHub release from the CHANGELOG section and attaches
   the built files.

## One-time setup

These need an account owner. The workflow can't do them itself.

1. **PyPI trusted publisher.** The project doesn't exist on PyPI yet, so add a *pending*
   publisher at <https://pypi.org/manage/account/publishing/> with:

   | Field | Value |
   |---|---|
   | PyPI project name | `linked-archi-mcp` |
   | Owner | `linked-archi` |
   | Repository name | `linked-archi-mcp` |
   | Workflow name | `release.yml` |
   | Environment name | `pypi` |

   The first successful upload creates the project and turns this into an ordinary
   trusted publisher.

2. **GitHub environment `pypi`.** Create it under Settings → Environments. Optionally
   add required reviewers, so every PyPI upload waits for an approval. If it doesn't
   exist, GitHub creates it with no protection rules the first time the workflow runs.

3. **Container image visibility.** GHCR creates the package as private on the first push.
   Under the organization's Packages → `linked-archi-mcp` → Package settings, change the
   visibility to public, or every `docker pull` will need a login. The image's
   `org.opencontainers.image.source` label links the package to this repository.

4. **MCP Registry.** No setup. GitHub OIDC from a workflow in a `linked-archi` repository
   grants the `io.github.linked-archi/*` namespace. Ownership of the PyPI package is
   proven by the `mcp-name:` comment at the top of README.md, which becomes the PyPI
   description.

## Each release

1. Set the version in `src/linked_archi_mcp/__init__.py`, and in `server.json` (top-level
   `version` and `packages[0].version`). The tests fail if they disagree.
2. If the skills should move, update the submodule pin and the version the README and
   CHANGELOG name:

   ```bash
   git -C vendor/linked-archi-apm fetch --tags && git -C vendor/linked-archi-apm checkout vA.B.C
   ```

3. Head the CHANGELOG section `## X.Y.Z - YYYY-MM-DD`.
4. Merge to `main` and wait for CI. Its `package`, `install` and `image` jobs run the same
   build and smoke tests as the release.
5. Tag the merge commit and push the tag:

   ```bash
   git tag vX.Y.Z origin/main && git push origin vX.Y.Z
   ```

## When a job fails

- **build:** nothing was published. Fix it on `main`, then move the tag:
  `git push --delete origin vX.Y.Z`, then tag and push again.
- **pypi:** usually the trusted publisher doesn't match the table above. Fix it on PyPI,
  then re-run the failed jobs from the workflow run. PyPI versions are immutable, so once
  an upload has succeeded, any change needs a new version.
- **registry**, **image** or **github-release:** fix the cause and re-run the failed job.
  `registry` runs only after `pypi` succeeds, and `github-release` only after both `pypi`
  and `image` succeed.
