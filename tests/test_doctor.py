from protoloom.doctor import _module_available, diagnose, format_report


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


def test_doctor_accepts_bundled_protoc_without_system_executable() -> None:
    modules = {"google.protobuf", "grpc_tools.protoc"}

    report = diagnose(
        which=lambda _: None,
        module_available=lambda name: name in modules,
    )

    compiler = next(item for item in report.dependencies if item.name == "protoc")
    assert report.healthy
    assert compiler.available
    assert (
        compiler.location
        == "python -m grpc_tools.protoc (not the pinned protoc binary)"
    )
    assert (
        "[ok] protoc — required "
        "(python -m grpc_tools.protoc (not the pinned protoc binary))"
        in format_report(report)
    )


def test_module_available_treats_a_missing_parent_package_as_unavailable() -> None:
    # find_spec raises ModuleNotFoundError (not just returning None) when
    # the parent package of a dotted name doesn't exist at all.
    assert _module_available("nonexistent.sub.module") is False
