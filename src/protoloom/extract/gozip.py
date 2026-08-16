import gzip

from protoloom.extract.descriptor import DescriptorFinding, scan_descriptors


def scan_gzip_descriptors(
    data: bytes, source: str = "binary"
) -> list[DescriptorFinding]:
    findings: list[DescriptorFinding] = []
    start = 0
    while (offset := data.find(b"\x1f\x8b", start)) >= 0:
        try:
            inflated = gzip.decompress(data[offset:])
        except (EOFError, OSError):
            start = offset + 1
            continue
        nested = scan_descriptors(inflated, f"{source}:gzip@0x{offset:x}")
        findings.extend(nested)
        start = offset + 2
    return findings
