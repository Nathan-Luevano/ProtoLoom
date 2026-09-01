from protoloom.extract.gotags import GoProtobufTag, parse_protobuf_tag


def test_parses_go_protobuf_struct_tag() -> None:
    tag = parse_protobuf_tag(
        'protobuf:"zigzag32,3,rep,packed,name=samples,proto3" json:"samples,omitempty"'
    )
    assert tag == GoProtobufTag("zigzag32", 3, "repeated", "samples", True, True)


def test_rejects_missing_or_malformed_protobuf_tag() -> None:
    assert parse_protobuf_tag('json:"value"') is None
    assert parse_protobuf_tag('protobuf:"varint,nope,opt,name=value"') is None
    assert parse_protobuf_tag('protobuf:"varint,1,unknown,name=value"') is None
    assert parse_protobuf_tag('protobuf:"varint,0,opt,name=value"') is None
