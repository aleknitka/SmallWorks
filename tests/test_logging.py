"""Logging tests: DEBUG sinks on console+file with rich bound context."""

from pathlib import Path

from smallworks.logging import configure_logging


def test_logging_writes_debug_to_file(tmp_path: Path, capsys):
    target = configure_logging(level="DEBUG", log_file=tmp_path / "sw.log", force=True)
    from smallworks.logging import logger

    logger.bind(component="test", task_id="AUTH-017").debug("hello {}", "world")
    body = (tmp_path / "sw.log").read_text(encoding="utf-8")
    assert "hello world" in body
    assert "test" in body and "AUTH-017" in body  # bound extra survives to the file sink
    assert target == tmp_path / "sw.log"
