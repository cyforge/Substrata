# Publishing the SDK and CLI

The Python distribution is named `substrata-sdk`. It contains the `substrata`
import package and the `substrata` console command, so one release ships both.
The optional Stellar integration is included through `substrata-sdk[stellar]`.

## Configure PyPI once

Sign in to your PyPI account and open [Publishing](https://pypi.org/manage/account/publishing/).
Create a pending trusted publisher with these exact values:

| Field | Value |
| --- | --- |
| PyPI project name | `substrata-sdk` |
| Owner | `cyforge` |
| Repository | `Substrata` |
| Workflow filename | `publish.yml` |
| Environment name | `pypi` |

The repository includes `.github/workflows/publish.yml`. It uses GitHub's OIDC
identity to publish, so there is no PyPI API token to store in GitHub secrets.
Only the publish job receives `id-token: write`.

A pending publisher does not reserve the package name. Check availability again
before the first release. If you already own `substrata-sdk` on PyPI, add the
publisher under that project's settings instead.

See PyPI's [trusted publisher setup](https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/)
for the account configuration.

## Prepare a release

Update the version in `pyproject.toml` and the source-checkout fallback in
`substrata/__init__.py`. Add release notes to `CHANGELOG.md`, then run:

```sh
python -m pip install -e ".[dev,stellar]" twine
python -m pytest
python -m ruff check .
python -m build
python -m twine check dist/*
```

Use a clean `dist/` directory for each version. Check the wheel in a fresh
environment before releasing:

```sh
python -m venv /tmp/substrata-release-check
/tmp/substrata-release-check/bin/python -m pip install dist/substrata_sdk-0.1.0-py3-none-any.whl
/tmp/substrata-release-check/bin/substrata --version
/tmp/substrata-release-check/bin/python -c "import substrata; print(substrata.__version__)"
```

Replace `0.1.0` with the release version. Website files and Node.js dependencies
are excluded from the Python source archive.

## Publish a release

After the version commit is on `main` and CI passes, create the matching tag and
publish a GitHub release. For version `0.1.0`:

```sh
git tag v0.1.0
git push origin v0.1.0
gh release create v0.1.0 --verify-tag --title "v0.1.0" --notes-file RELEASE_NOTES.md
```

Create `RELEASE_NOTES.md` with the version's release notes before running the last
command. Publishing the GitHub release triggers the workflow. It checks that
the tag matches the package version, runs tests and lint, builds and validates
the distributions, then uploads those artifacts to PyPI.

Configure the trusted publisher before publishing the GitHub release. If the
upload fails because the publisher was missing, configure it and rerun the
failed workflow. An existing PyPI version cannot be overwritten; use a new
version for changed package contents.

## Verify installation

After a successful upload, consumers can install the SDK:

```sh
python -m pip install substrata-sdk
python -m pip install "substrata-sdk[stellar]"
```

For an isolated CLI install, use `pipx install substrata-sdk` or
`uv tool install substrata-sdk`. Both expose the `substrata` command. The command's
Stellar integration needs the extra: `uv tool install "substrata-sdk[stellar]"`.

```sh
substrata --version
substrata demo --database demo.sqlite3
```

Until the first PyPI release succeeds, use the [repository installation](installation.md).
