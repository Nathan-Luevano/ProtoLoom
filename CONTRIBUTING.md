# Contributing

Run `make check` before proposing a change. Tests should use the original schema
as a differential oracle where one exists, and recovered payloads must
byte-identically round-trip before a recovery claim is treated as confirmed.

The project contains no Python docstrings. Use rare, informal comments to
explain a surprising reason, not to narrate the code.

## Commits

Keep one logical change in each commit and use:

```text
<type>(<scope>): <imperative summary under 72 chars>

<why the change exists, wrapped at 80 columns>
```

Types are `feat`, `fix`, `refactor`, `test`, `bench`, `docs`, `chore`, and
`perf`. Scopes are `dex`, `lite`, `descriptor`, `native`, `emit`, `bench`,
`cli`, and `corpus`.

Do not add co-author, generator, or foreign sign-off attribution. Commits are
authored by Nathan Luevano. Any change to measured accuracy must give the before
and after numbers and update `benchmarks/results.md` in the same commit.

