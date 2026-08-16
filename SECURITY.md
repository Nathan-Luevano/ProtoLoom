# Security policy

## Reporting a vulnerability

Please report security issues privately through GitHub's security advisory
form. Do not open a public issue for a vulnerability or attach a sensitive
binary to an issue.

Include the affected version or commit, a minimal reproducer, impact, and any
suggested mitigation. You can expect an acknowledgement within seven days.

Only the latest released version and the default branch receive security fixes.
PROTOLOOM processes hostile binaries by design, so parser crashes, excessive
resource use, path traversal, and unsafe archive handling are all in scope.

## Benchmark artifacts

Real-app APKs are never stored in this repository. The corpus manifest points
only to official HTTPS release assets and pins both their byte size and SHA-256.
The fetch script writes through a temporary file, verifies before an atomic
rename, refuses symlink targets, and will not replace an existing mismatch.

Treat every downloaded APK and captured payload as hostile. Keep benchmark
inputs outside the source tree, do not execute them, and do not upload private
traffic or proprietary applications in bug reports. A matching hash establishes
reproducibility, not trustworthiness.
