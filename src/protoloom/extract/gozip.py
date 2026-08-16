import zlib

from protoloom.extract.descriptor import DescriptorFinding, scan_descriptors


def scan_gzip_descriptors(
    data: bytes, source: str = "binary", max_inflated_size: int = 64 * 1024 * 1024
) -> list[DescriptorFinding]:
    if max_inflated_size <= 0:
        raise ValueError("max inflated size must be positive")
    findings: list[DescriptorFinding] = []
    start = 0
    while (offset := data.find(b"\x1f\x8b", start)) >= 0:
        try:
            inflater = zlib.decompressobj(wbits=31)
            inflated = inflater.decompress(data[offset:], max_inflated_size + 1)
            if not inflater.eof or len(inflated) > max_inflated_size:
                start = offset + 1
                continue
        except zlib.error:
            start = offset + 1
            continue
        nested = scan_descriptors(inflated, f"{source}:gzip@0x{offset:x}")
        findings.extend(nested)
        start = offset + 2
    return findings
