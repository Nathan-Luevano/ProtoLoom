import zlib

from protoloom.extract.descriptor import DescriptorFinding, scan_descriptors


class _Budget:
    __slots__ = ("remaining",)

    def __init__(self, remaining: int) -> None:
        self.remaining = remaining


def scan_gzip_descriptors(
    data: bytes,
    source: str = "binary",
    max_inflated_size: int = 64 * 1024 * 1024,
    max_members: int = 256,
    max_depth: int = 4,
) -> list[DescriptorFinding]:
    if max_inflated_size <= 0:
        raise ValueError("max inflated size must be positive")
    if max_members <= 0:
        raise ValueError("max gzip members must be positive")
    if max_depth <= 0:
        raise ValueError("max gzip depth must be positive")
    return _scan_gzip(data, source, _Budget(max_inflated_size), max_members, max_depth)


def _scan_gzip(
    data: bytes, source: str, budget: _Budget, max_members: int, depth: int
) -> list[DescriptorFinding]:
    # A gzip member's own payload can itself be gzip (protoc-gen-go emits a
    # single level, but bundlers can wrap that again); the shared budget
    # bounds total inflation across every nesting level so depth doesn't
    # multiply the decompression-bomb ceiling max_inflated_size already caps.
    findings: list[DescriptorFinding] = []
    start = 0
    members = 0
    while budget.remaining and (offset := data.find(b"\x1f\x8b", start)) >= 0:
        members += 1
        if members > max_members:
            break
        try:
            inflater = zlib.decompressobj(wbits=31)
            inflated = inflater.decompress(data[offset:], budget.remaining + 1)
            if len(inflated) > budget.remaining:
                break
            budget.remaining -= len(inflated)
            if not inflater.eof:
                start = offset + 1
                continue
        except zlib.error:
            start = offset + 1
            continue
        nested_source = f"{source}:gzip@0x{offset:x}"
        findings.extend(scan_descriptors(inflated, nested_source))
        if depth > 1:
            findings.extend(
                _scan_gzip(inflated, nested_source, budget, max_members, depth - 1)
            )
        start = offset + 2
    return findings
