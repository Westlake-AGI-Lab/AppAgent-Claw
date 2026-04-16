import re

from scripts.schema import FlowDefinition, FlowStep, Target
from scripts.storage import Storage


def build_flow() -> FlowDefinition:
    return FlowDefinition(
        name="phase-one-smoke",
        created_at="2026-03-19T12:00:00Z",
        steps=[
            FlowStep(
                id="step_0001",
                action="move",
                target=Target(abs_x=10, abs_y=20, rel_x=0.1, rel_y=0.2),
            )
        ],
    )


def test_create_recording_and_run_dirs_follow_naming(tmp_path) -> None:
    storage = Storage(tmp_path / "data")

    recording_dir = storage.create_recording("Google Login")
    run_dir = storage.create_run("Google Login")

    assert re.match(r"\d{8}_\d{6}_Google-Login", recording_dir.name)
    assert re.match(r"\d{8}T\d{6}Z_Google-Login", run_dir.name)
    assert (recording_dir / "assets").is_dir()


def test_save_and_load_flow_round_trip(tmp_path) -> None:
    storage = Storage(tmp_path / "data")
    recording_dir = storage.create_recording("phase-one")
    flow = build_flow()

    flow_path = storage.save_flow(recording_dir, flow)
    loaded = storage.load_flow(flow_path)

    assert loaded.to_dict() == flow.to_dict()


def test_step_asset_dir_creates_nested_step_folder(tmp_path) -> None:
    storage = Storage(tmp_path / "data")
    recording_dir = storage.create_recording("phase-one")

    step_dir = storage.step_asset_dir(recording_dir, "step_0001")

    assert step_dir == recording_dir / "assets" / "step_0001"
    assert step_dir.is_dir()


def test_create_recording_and_run_dirs_are_unique_with_same_timestamp(tmp_path) -> None:
    storage = Storage(tmp_path / "data")
    storage._recording_timestamp = lambda: "20260319_120000"
    storage._run_timestamp = lambda: "20260319T120000Z"

    recording_dir_1 = storage.create_recording("phase-one")
    recording_dir_2 = storage.create_recording("phase-one")
    run_dir_1 = storage.create_run("phase-one")
    run_dir_2 = storage.create_run("phase-one")

    assert recording_dir_1.name == "20260319_120000_phase-one"
    assert recording_dir_2.name == "20260319_120000_phase-one_2"
    assert run_dir_1.name == "20260319T120000Z_phase-one"
    assert run_dir_2.name == "20260319T120000Z_phase-one_2"
