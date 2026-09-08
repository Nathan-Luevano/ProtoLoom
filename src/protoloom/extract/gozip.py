import zlib

from protoloom.extract.descriptor import DescriptorFinding, scan_descriptors


def scan_gzip_descriptors(
    data: bytes,
    source: str = "binary",
    max_inflated_size: int = 64 * 1024 * 1024,
    max_members: int = 256,
) -> list[DescriptorFinding]:
    if max_inflated_size <= 0:
        raise ValueError("max inflated size must be positive")
    if max_members <= 0:
        raise ValueError("max gzip members must be positive")
    findings: list[DescriptorFinding] = []
    start = 0
    remaining = max_inflated_size
    members = 0
    while remaining and (offset := data.find(b"\x1f\x8b", start)) >= 0:
        members += 1
        if members > max_members:
            break
        try:
            inflater = zlib.decompressobj(wbits=31)
            inflated = inflater.decompress(data[offset:], remaining + 1)
            if len(inflated) > remaining:
                break
            remaining -= len(inflated)
            if not inflater.eof:
                start = offset + 1
                continue
        except zlib.error:
            start = offset + 1
            continue
        nested = scan_descriptors(inflated, f"{source}:gzip@0x{offset:x}")
        findings.extend(nested)
        start = offset + 2
    return findings
