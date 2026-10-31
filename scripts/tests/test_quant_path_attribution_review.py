import importlib.util
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('path_review', ROOT/'scripts/quant-path-attribution-review.py')
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)


def test_wrong_or_revised_input_refused_before_report(tmp_path):
    source = tmp_path/'input.json'; source.write_text('[]')
    output = tmp_path/'report.json'
    with pytest.raises(ValueError, match='identity'):
        cli.main(['--input', str(source), '--output', str(output)])
    assert not output.exists()


def test_existing_report_never_overwritten_even_with_invalid_input(tmp_path):
    output = tmp_path/'report.json'; output.write_bytes(b'preserve failed research')
    with pytest.raises(SystemExit):
        cli.main(['--input', str(tmp_path/'missing'), '--output', str(output)])
    assert output.read_bytes() == b'preserve failed research'


def test_protocol_byte_revision_rejected():
    # The tracked protocol, not a caller's mutable economic specification.
    body = cli.retained(ROOT/'docs/quant-path-attribution-protocol-20261101.json', cli.PROTOCOL_SHA)
    assert b'no_parameter_search' in body


def test_symlink_not_accepted_as_retained_input(tmp_path):
    source = tmp_path/'source'; source.write_bytes(b'[]')
    link = tmp_path/'link'; link.symlink_to(source)
    with pytest.raises(ValueError, match='regular'): cli.retained(link, cli.INPUT_SHA)


def test_oversized_retained_input_rejected(tmp_path):
    source = tmp_path/'source'; source.write_bytes(b'x'*(4*1024**2+1))
    with pytest.raises(ValueError, match='Bounded'): cli.retained(source, cli.INPUT_SHA)
