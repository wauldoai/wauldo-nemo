# Publishing wauldo-nemo to PyPI

> **Blocker:** wauldo-nemo depends on `wauldo>=0.19`. That SDK version must be
> live on PyPI **before** wauldo-nemo is published, otherwise `pip install
> wauldo-nemo` fails to resolve its dependency. Check first:
>
> ```bash
> pip index versions wauldo   # must list >= 0.19.0
> ```

## 1. Pre-flight

```bash
cd wauldo-nemo
pip install -e '.[dev]'
pytest                      # policy tests must pass
```

Bump `version` in `pyproject.toml` **and** `__version__` in
`src/wauldo_nemo/__init__.py` (keep them in lockstep), and add a CHANGELOG
entry.

## 2. Build

```bash
python -m pip install --upgrade build twine
python -m build             # writes dist/*.whl + dist/*.tar.gz
twine check dist/*          # metadata sanity
```

## 3. Publish

```bash
# TestPyPI first (recommended)
twine upload --repository testpypi dist/*
pip install --index-url https://test.pypi.org/simple/ \
    --extra-index-url https://pypi.org/simple/ wauldo-nemo

# Then the real index
twine upload dist/*
```

## 4. Tag

```bash
git tag -a v0.1.0 -m "wauldo-nemo 0.1.0"
git push origin v0.1.0
```

## Notes

- The `hatchling<1.27` pin mirrors the `wauldo` SDK (avoids a metadata
  regression in newer hatchling).
- `nemoguardrails` is an **optional** extra (`pip install 'wauldo-nemo[nemo]'`),
  not a hard dependency — importing the policy core and running the unit tests
  must not require the (heavy) guardrails runtime.
