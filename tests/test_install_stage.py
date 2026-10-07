"""Install staging gate (issue #8): every module the console or the
operator CLI imports at runtime must be staged into the share by
install.sh. minder_domain_evals (rulebook provenance) and benchmarks/
(suite manifests + baselines) were missing, which left the live
/domains page half-empty while every test stayed green."""
from pathlib import Path

INSTALL = Path(__file__).resolve().parents[1] / "install.sh"

# modules staged with cp -R; files with cp -f are covered separately
STAGED_DIRS = (
    "minder_memory", "minder_decision", "minder_core", "minder_op",
    "minder_web", "minder_trace", "minder_domain_evals",
    "benchmarks", "skills", "presets",
)


def test_install_stages_every_runtime_module():
    script = INSTALL.read_text()
    stage_block = script[script.index("[1] stage code"):
                         script.index("hooks.json: patch")]
    for module in STAGED_DIRS:
        assert f'cp -R "$SRC/{module}" "$SHARE/"' in stage_block, \
            f"install.sh does not stage {module}/ - the live console " \
            "will degrade where the repo tests pass (M2)"
