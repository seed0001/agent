from src.agent.core import _normalize_tool_name, _recover_text_tool_call


def test_normalizes_near_miss_tool_names():
    assert _normalize_tool_name("Write_file") == "write_file"
    assert _normalize_tool_name("write file") == "write_file"
    assert _normalize_tool_name("write_file.") == "write_file"
    assert _normalize_tool_name("check file exists") == "verify_file_exists"
    assert _normalize_tool_name("Run Shell") == "run_command"


def test_recovers_save_to_write_file_text_form():
    recovered = _recover_text_tool_call(
        r"Save to C:\Users\aztre\Desktop\agent\andrew's projects\test.txt: hello Travis"
    )

    assert recovered is not None
    assert recovered["name"] == "write_file"
    assert recovered["args"]["path"] == r"C:\Users\aztre\Desktop\agent\andrew's projects\test.txt"
    assert recovered["args"]["content"] == "hello Travis"


def test_recovers_create_file_text_form():
    recovered = _recover_text_tool_call(
        r"Create file C:\tmp\note.txt containing this is the content"
    )

    assert recovered is not None
    assert recovered["name"] == "write_file"
    assert recovered["args"] == {
        "path": r"C:\tmp\note.txt",
        "content": "this is the content",
    }


def test_recovers_verify_and_read_text_forms():
    verify = _recover_text_tool_call(r"Check if file exists at C:\tmp\note.txt")
    read = _recover_text_tool_call(r"Open file at C:\tmp\note.txt")

    assert verify is not None
    assert verify["name"] == "verify_file_exists"
    assert verify["args"]["path"] == r"C:\tmp\note.txt"
    assert read is not None
    assert read["name"] == "read_file"
    assert read["args"]["path"] == r"C:\tmp\note.txt"


def test_recovers_run_command_text_form():
    recovered = _recover_text_tool_call("Execute command: python --version")

    assert recovered is not None
    assert recovered["name"] == "run_command"
    assert recovered["args"]["cmd"] == "python --version"


def test_does_not_recover_narrative_success_claim():
    recovered = _recover_text_tool_call(
        r"I've created the file at C:\tmp\note.txt and saved it for you."
    )

    assert recovered is None
