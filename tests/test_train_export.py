"""Training export gate tests (docs/minder-phase-4-7-frontier-coding.md
P7.1). Export is a dataset, not a deployed adapter: every candidate is a
verified episode + verified non-superseded lesson with local tests_passed,
a redacted compact trajectory, and family support. No GGUF, no
llama-server calls, no systemd units — ever."""
import json

from memory import (db as _db, frontier_traces, graph, lessons, store,
                    train_export)

REPO = "/repo"
SECRET = "sk-proj-zzzsecret9876543210"


def verified_pair(dbp, sym, family="keyerror", secret=False):
    """One verified episode + verified lesson in the given failure family."""
    key = f"bash|{family}|{sym}|app/m.py"
    ep_id, _ = store.open_episode({"repo": REPO, "task_id": f"t-{sym}"},
                                  db_path=dbp)
    excerpt = f"KeyError: '{sym}'"
    if secret:
        excerpt += f" env key {SECRET}"
    store.add_attempt(ep_id, {"event_type": "tool_failure", "tool": "bash",
                              "repo": REPO, "failure_key": key,
                              "error_excerpt": excerpt,
                              "args_json": '{"command": "python3 -m '
                                           'pytest tests/test_x.py"}'},
                      db_path=dbp)
    store.close_episode(ep_id, "verified", db_path=dbp)
    lesson, status = lessons.promote_lesson(
        ep_id, f"guard {sym} lookups with .get", verification={"tests_passed": True},
        repo=REPO, failure_key=key, db_path=dbp)
    assert status == "ok"
    return ep_id, lesson["lesson_id"]


def seed_family(dbp, n, family="keyerror", secret=False):
    return [verified_pair(dbp, f"sym{i}", family=family, secret=secret)
            for i in range(n)]


def test_unverified_episode_excluded(tmp_path):
    dbp = tmp_path / "m.sqlite"
    ep_id, lesson_id = verified_pair(dbp, "a")
    seed_family(dbp, 4, )  # family support met overall
    # demote the first episode below verified after its lesson exists
    store.close_episode(ep_id, "candidate", db_path=dbp)
    out = train_export.export_training_candidates(db_path=dbp,
                                                  out_dir=tmp_path / "exp")
    assert all(c["episode_id"] != ep_id for c in out)


def test_secret_in_excerpt_never_reaches_trajectory(tmp_path):
    dbp = tmp_path / "m.sqlite"
    seed_family(dbp, 5, secret=True)
    out = train_export.export_training_candidates(db_path=dbp,
                                                  out_dir=tmp_path / "exp")
    assert out
    assert SECRET not in json.dumps(out)
    for cand in out:
        for step in cand["trajectory"]:
            assert SECRET not in step["detail"]


def test_harmful_consult_episode_excluded(tmp_path):
    dbp = tmp_path / "m.sqlite"
    pairs = seed_family(dbp, 5)
    bad_ep, _bad_lesson = pairs[0]
    tid = frontier_traces.record_consult(
        {"key": f"bash|keyerror|sym0|app/m.py", "attempts": 2,
         "episode_id": bad_ep, "prompt": "fix", "response": "advice"},
        db_path=dbp)
    frontier_traces.classify_consult(tid, "fail", accepted=["bad advice"],
                                     db_path=dbp)
    out = train_export.export_training_candidates(db_path=dbp,
                                                  out_dir=tmp_path / "exp")
    assert all(c["episode_id"] != bad_ep for c in out)
    assert len(out) == 4


def test_min_family_count_gate(tmp_path):
    dbp = tmp_path / "m.sqlite"
    seed_family(dbp, 4)
    out = train_export.export_training_candidates(db_path=dbp,
                                                  out_dir=tmp_path / "exp")
    assert out == []  # 4 verified episodes < min_family_count=5
    forced = train_export.export_training_candidates(db_path=dbp, force=True,
                                                     out_dir=tmp_path / "exp2")
    assert len(forced) == 4  # operator force (tests only) bypasses the gate


def test_five_verified_export_with_held_out(tmp_path):
    dbp = tmp_path / "m.sqlite"
    seed_family(dbp, 5)
    out = train_export.export_training_candidates(db_path=dbp,
                                                  out_dir=tmp_path / "exp")
    assert len(out) == 5
    assert any(c["split"] == "held_out" for c in out)  # >= 1 (or ~20%)
    assert all(c["trajectory"][0]["step"] == "inspect" for c in out)
    assert all([s["step"] for s in c["trajectory"]][:4] ==
               ["inspect", "hypothesis", "patch", "tests"] for c in out)
    # rows persisted; re-export is idempotent
    conn = _db.connect(dbp)
    try:
        n = conn.execute("SELECT COUNT(*) AS n FROM training_candidates") \
            .fetchone()["n"]
    finally:
        conn.close()
    assert n == 5
    again = train_export.export_training_candidates(db_path=dbp,
                                                    out_dir=tmp_path / "exp")
    assert {c["candidate_id"] for c in again} == \
        {c["candidate_id"] for c in out}


def test_environment_family_and_superseded_excluded(tmp_path):
    dbp = tmp_path / "m.sqlite"
    seed_family(dbp, 5, family="permissionerror")
    out = train_export.export_training_candidates(db_path=dbp,
                                                  out_dir=tmp_path / "exp")
    assert out == []  # environment/permissions-only episodes never export

    dbp2 = tmp_path / "m2.sqlite"
    pairs = seed_family(dbp2, 5)
    old_ep, old_lesson = pairs[0]
    # a verified lesson with an incoming SUPERSEDES edge is excluded even
    # though its row still says verified
    graph.link(pairs[1][1], "SUPERSEDES", old_lesson, db_path=dbp2)
    out = train_export.export_training_candidates(db_path=dbp2,
                                                  out_dir=tmp_path / "exp")
    assert all(c["lesson_id"] != old_lesson for c in out)
    assert len(out) == 4


def test_decision_trace_id_linked_when_present(tmp_path):
    from decision import trace as dtrace
    dbp = tmp_path / "m.sqlite"
    pairs = seed_family(dbp, 5)
    linked_ep, _ = pairs[0]
    key = "bash|keyerror|sym0|app/m.py"
    dtrace.record_decision(db_path=dbp, contract_id="failure-triage",
                           contract_version="v1", session_id="s1",
                           failure_key=key, state="{}",
                           menu=["inspect", "human"])
    out = train_export.export_training_candidates(db_path=dbp,
                                                  out_dir=tmp_path / "exp")
    by_episode = {c["episode_id"]: c for c in out}
    assert by_episode[linked_ep]["decision_trace_id"]
    blob = json.dumps(out)
    assert "decision_trace_id" in blob
    for cand in out:
        if cand["episode_id"] != linked_ep:
            assert cand["decision_trace_id"] is None
    # a trace NEVER rescues an unverified episode
    store.close_episode(linked_ep, "candidate", db_path=dbp)
    out2 = train_export.export_training_candidates(db_path=dbp,
                                                   out_dir=tmp_path / "exp2")
    assert all(c["episode_id"] != linked_ep for c in out2)


def test_accepted_actions_may_appear_raw_response_never(tmp_path):
    dbp = tmp_path / "m.sqlite"
    pairs = seed_family(dbp, 5)
    ep_id = pairs[0][0]
    tid = frontier_traces.record_consult(
        {"key": "bash|keyerror|sym0|app/m.py", "episode_id": ep_id,
         "prompt": "fix", "response": "RAW-MARKER-refactor-everything",
         "distilled": ["check dict defaults"]}, db_path=dbp)
    frontier_traces.classify_consult(tid, "pass",
                                     accepted=["check dict defaults"],
                                     db_path=dbp)
    out = train_export.export_training_candidates(db_path=dbp,
                                                  out_dir=tmp_path / "exp")
    blob = json.dumps(out)
    assert "RAW-MARKER-refactor-everything" not in blob  # no raw response
    assert "check dict defaults" in blob  # accepted actions allowed


def test_no_gguf_no_llama_calls_no_systemd(tmp_path):
    import ast
    dbp = tmp_path / "m.sqlite"
    seed_family(dbp, 5)
    out_dir = tmp_path / "exp"
    train_export.export_training_candidates(db_path=dbp, out_dir=out_dir)
    produced = sorted(p.name for p in out_dir.iterdir())
    assert produced == ["training_candidates.jsonl"]
    assert not any(p.suffix == ".gguf" for p in out_dir.rglob("*"))
    # AST-level pin: the module imports no trainer/shell surface at all
    tree = ast.parse(open(train_export.__file__).read())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert imported <= {"hashlib", "json", "datetime", "pathlib", "memory",
                        "canonicalise", "decision"}
