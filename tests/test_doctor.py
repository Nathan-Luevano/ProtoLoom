from protoloom.doctor import diagnose, format_report


def test_doctor_distinguishes_required_and_optional_dependencies() -> None:
    executables = {"protoc": "/tools/protoc", "java": "/tools/java"}
    modules = {"google.protobuf"}
    report = diagnose(
        which=executables.get,
        module_available=lambda name: name in modules,
    )

    assert report.healthy
    assert {item.name for item in report.missing_optional} == {
        "jadx",
        "lief",
        "androguard",
    }
    rendered = format_report(report)
    assert "[ok] protoc" in rendered
    assert "[missing] jadx" in rendered
    assert rendered.endswith("Ready.\n")


def test_doctor_is_unhealthy_without_required_dependency() -> None:
    report = diagnose(which=lambda _: None, module_available=lambda _: False)
    assert not report.healthy
    assert format_report(report).endswith("Missing required dependencies.\n")
