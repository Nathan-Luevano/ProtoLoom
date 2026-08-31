import importlib.util
from pathlib import Path

from google.protobuf import descriptor_pb2

path = Path(__file__).parents[1] / "scripts" / "check_matrix_roundtrip.py"
spec = importlib.util.spec_from_file_location("check_matrix_roundtrip", path)
if spec is None or spec.loader is None:
    raise RuntimeError("cannot load check_matrix_roundtrip.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
package_descriptor_set = module.package_descriptor_set


def test_package_descriptor_set_excludes_unrelated_dependencies() -> None:
    descriptors = descriptor_pb2.FileDescriptorSet()
    runtime = descriptors.file.add(
        name="runtime.proto",
        package="com.google.protobuf",
        dependency=["missing.proto"],
    )
    runtime.message_type.add(name="Runtime")
    matrix = descriptors.file.add(name="matrix.proto", package="matrix")
    matrix.message_type.add(name="Everything")

    selected = package_descriptor_set(descriptors, "matrix")

    assert [file.name for file in selected.file] == ["matrix.proto"]
    assert selected.file[0].message_type[0].name == "Everything"
