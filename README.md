# PROTOLOOM

Recovers compilable `.proto` schemas from stripped Android, native, and Go
binaries. Measured against real ground truth, not vibes.

PROTOLOOM is in early development. The current scaffold establishes the shared
recovery model and the quality gates that every extractor will have to pass.

## Development

Create the reproducible environment with micromamba:

```console
micromamba create -y -p ./.venv -f environment.yml
micromamba run -p ./.venv make check
```

For a faster Python-only setup, run `uv sync --extra dev` followed by
`uv run make check`; it uses the same dependency declaration.

## License

Apache License 2.0.
