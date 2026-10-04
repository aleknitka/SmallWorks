"""Context tests: budget enforcement, no repo dump for developers, RTK recall (plan 03)."""

from smallworks.adapters.caveman import caveman_report
from smallworks.adapters.repomix import repo_overview
from smallworks.adapters.rtk import RecallStore, compress_output, recall_output
from smallworks.adapters.serena import query_serena
from smallworks.context import PacketSource, build_packet


def _packet_len(packet) -> int:
    return sum(
        len(s)
        for s in (
            [packet.goal, packet.module_contract]
            + packet.relevant_symbols
            + packet.related_tests
            + packet.coding_rules
        )
    )


def test_packet_contains_all_sections_within_budget():
    res = query_serena("src/auth/token.py")
    packet, dropped = build_packet(
        task_id="AUTH-017",
        goal="handle expired token",
        module_contract="validate(token) -> bool; expired -> 401",
        symbols=res.symbols,
        related_tests=["tests/test_token.py::test_expired"],
        coding_rules=["no prose in reports"],
        sources=[PacketSource(kind="symbols", ref="serena:token", content="validate, refresh")],
        budget_chars=8_000,
    )
    assert packet.goal and packet.module_contract
    assert packet.relevant_symbols and packet.related_tests and packet.coding_rules
    assert _packet_len(packet) <= 8_000
    assert dropped == []


def test_budget_truncates_with_refs():
    big = "x" * 5_000
    packet, dropped = build_packet(
        task_id="AUTH-017",
        goal="g",
        module_contract="c",
        symbols=["sym0"],
        sources=[
            PacketSource(kind="source", ref="src/a.py", content=big),
            PacketSource(kind="source", ref="src/b.py", content=big),
        ],
        budget_chars=1_000,
    )
    assert _packet_len(packet) <= 1_000
    assert dropped and any(d.startswith("ref:src/") for d in dropped)


def test_developer_packet_excludes_repo_dump():
    overview = repo_overview("demo", modules=["auth", "billing"])
    packet, _ = build_packet(
        task_id="AUTH-017",
        goal="g",
        module_contract="c",
        sources=[overview],
    )
    assert all("repomix" not in s for s in packet.relevant_symbols)
    packet2, _ = build_packet(
        task_id="AUTH-017",
        goal="g",
        module_contract="c",
        sources=[overview],
        include_repo_overview=True,
    )
    assert any("repomix" in s for s in packet2.relevant_symbols)


def test_rtk_compress_roundtrips_to_raw():
    store = RecallStore()
    raw = "\n".join(f"line {i}" for i in range(100))
    summary, ref = compress_output(raw, store)
    assert "omitted" in summary
    assert recall_output(ref, store) == raw


def test_rtk_short_output_uncompressed():
    store = RecallStore()
    summary, ref = compress_output("ok\npass", store)
    assert summary == "ok\npass"
    assert recall_output(ref, store) == "ok\npass"


def test_caveman_report_structured_no_prose():
    report = caveman_report("failed", "test_expired_token: expected 401 got 500", "developer_retry",
                            suspected_file="src/auth/token.py")
    assert report.status == "failed"
    assert report.next == "developer_retry"
    assert report.model_dump()["suspected_file"] == "src/auth/token.py"
