from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.batch_runner import load_test_inputs, run_batch


def test_load_test_inputs_parses_json_array(tmp_path: Path) -> None:
    test_file = tmp_path / "inputs.json"
    test_file.write_text(
        json.dumps(
            [
                {"name": "test_1", "prompt": "Epic brass score", "duration_s": 15},
                {"name": "test_2", "prompt": "Chill lofi beats", "duration_s": 25},
            ]
        ),
        encoding="utf-8",
    )
    cases = load_test_inputs(test_file)
    assert len(cases) == 2
    assert cases[0][0] == "test_1"
    assert cases[0][1]["prompt"] == "Epic brass score"
    assert cases[1][0] == "test_2"


def test_load_test_inputs_from_directory(tmp_path: Path) -> None:
    (tmp_path / "case_a.json").write_text(json.dumps({"prompt": "Ambient tone", "duration_s": 10}))
    (tmp_path / "case_b.json").write_text(json.dumps({"prompt": "Rock solo", "duration_s": 12}))
    cases = load_test_inputs(tmp_path)
    assert len(cases) == 2
    names = {c[0] for c in cases}
    assert "case_a" in names
    assert "case_b" in names


def test_batch_runner_preflight_checks_nonexistent_path() -> None:
    with pytest.raises(FileNotFoundError):
        load_test_inputs(Path("nonexistent_path_xyz"))


def test_test_pack_inputs_are_valid() -> None:
    root = Path(__file__).resolve().parents[2]
    diverse_file = root / "test_pack" / "inputs" / "10_diverse_prompts.json"
    hard_file = root / "test_pack" / "inputs" / "hard_cases.json"

    assert diverse_file.is_file()
    assert hard_file.is_file()

    diverse_cases = load_test_inputs(diverse_file)
    assert len(diverse_cases) == 10

    hard_cases = load_test_inputs(hard_file)
    assert len(hard_cases) >= 5
