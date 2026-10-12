"""Explicit test cost tiers, also loadable against an archived checkout with -p.

Default pytest still runs all collected tests. Fast excludes process isolation,
installed-wheel acceptance and optional real-torch serialization; its in-process
integrity, failure, timeline and immutable regression checks are unchanged.
"""

from pathlib import Path

import pytest


FULL_MODULES = {"test_packaging.py", "test_voice_clone_serialization.py"}
FULL_CASES = {
    "test_alignment.py": {"test_alignment_runtime_import_independence_is_subprocess_stable"},
    "test_cli.py": {
        "test_module_cli_writes_utf8_json_and_imports_no_model_runtime",
        "test_escaped_surrogate_json_still_produces_valid_utf8_output",
        "test_standard_console_entrypoint_and_unified_module_help",
    },
    "test_clone_prosody_analysis.py": {"test_analysis_import_does_not_require_torch_or_qwen"},
    "test_clone_prosody_runner.py": {
        "test_heavy_import_and_production_isolation",
        "test_module_plan_always_writes_utf8_even_under_ascii_environment",
    },
    "test_production_cli.py": {"test_module_cli_emits_utf8_json_and_runs_assembly_without_models"},
    "test_production.py": {"test_worker_nonzero_exit_preserves_prefix_without_reporting_success"},
    "test_qwen_voice_clone.py": {
        "test_asset_restores_in_fresh_process_without_qwen_or_reference",
        "test_fresh_process_translates_missing_torch_without_heavy_packages",
    },
    "test_render.py": {
        "test_embedded_tts_stage_builds_requests_and_synthesizes_each_segment_on_cpu",
        "test_run_model_stage_round_trip",
        "test_run_model_stage_runtime_failure_reports_runtime_error",
        "test_alignment_io_fault_reported_as_runtime_stop",
        "test_alignment_validation_fault_reported_as_isolated",
        "test_alignment_output_io_failure_stops_run_first_segment",
        "test_alignment_output_io_failure_preserves_prior_results",
        "test_alignment_unclassified_failure_preserves_segment_context",
        "test_alignment_deterministic_validation_still_continues",
        "test_run_model_stage_preserves_manifest_on_nonzero_exit",
        "test_isolated_interpreter_pins_package_without_shadowing_worker_dependencies",
    },
    "test_render_clone.py": {
        "test_static_clone_validation_never_imports_or_deserializes_heavy_runtime",
        "test_clone_stage_loads_asset_and_base_once_and_runs_existing_downstream",
        "test_clone_request_and_report_keep_authored_portable_voice_provenance",
        "test_clone_failures_preserve_structured_failed_render_and_original_reason",
    },
    "test_speech_pipeline_e2e_integration.py": {
        "test_standalone_driver_starts_from_repo_root_and_gates_on_env",
    },
    "test_tts.py": {"test_importing_regrain_does_not_pull_torch_or_qwen_or_soundfile"},
    "test_voice_clone.py": {"test_portable_and_runtime_imports_do_not_load_heavy_dependencies"},
}
GPU_MODULES = {"test_tts_integration.py", "test_alignment_integration.py"}
GPU_CASES = {"test_speech_pipeline_e2e_integration.py": {"test_full_pipeline_end_to_end"}}


def pytest_configure(config):
    for marker, description in (
        ("fast", "in-process CPU integrity, timeline, failure and regression checks"),
        ("full", "CPU process isolation, installed-wheel or optional real-torch checks"),
        ("gpu", "explicit real model integration; requires configured ai-core runtimes"),
    ):
        config.addinivalue_line("markers", f"{marker}: {description}")


def pytest_collection_modifyitems(items):
    for item in items:
        module = Path(str(item.path)).name
        name = item.originalname or item.name.split("[", 1)[0]
        if item.get_closest_marker("gpu") or module in GPU_MODULES or name in GPU_CASES.get(module, set()):
            tier = "gpu"
        elif item.get_closest_marker("full") or module in FULL_MODULES or name in FULL_CASES.get(module, set()):
            tier = "full"
        else:
            tier = "fast"
        item.add_marker(getattr(pytest.mark, tier))
