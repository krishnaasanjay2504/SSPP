"""Run student code against test cases in an isolated subprocess with a timeout.

Security note: this executes untrusted code on the server. Keep CODE_EXEC_ENABLED off on a
shared/public host unless the app runs inside a locked-down container or VM.
"""
import os
import shutil
import subprocess
import sys
import tempfile

LANGS = {
    "python": {"label": "Python 3", "file": "main.py", "run": [sys.executable, "-I", "main.py"]},
    "c": {"label": "C (gcc)", "file": "main.c", "build": ["gcc", "main.c", "-O2", "-o", "main"], "run": ["./main"], "need": "gcc"},
    "cpp": {"label": "C++ (g++)", "file": "main.cpp", "build": ["g++", "main.cpp", "-O2", "-o", "main"], "run": ["./main"], "need": "g++"},
    "java": {"label": "Java", "file": "Main.java", "build": ["javac", "Main.java"], "run": ["java", "-cp", ".", "Main"], "need": "javac"},
}


def available_languages():
    return {k: v["label"] for k, v in LANGS.items() if not v.get("need") or shutil.which(v["need"])}


def _norm(s):
    return "\n".join(line.rstrip() for line in (s or "").strip().splitlines())


def run(lang, code, tests, timeout=5):
    """tests: list of {"input":..., "output":...}. Returns dict(results, passed, total, verdict, compile_error)."""
    spec = LANGS.get(lang)
    if not spec or lang not in available_languages():
        return {"results": [], "passed": 0, "total": len(tests), "verdict": "UNSUPPORTED_LANGUAGE",
                "compile_error": f"{lang} is not installed on the server."}
    work = tempfile.mkdtemp(prefix="sspp_run_")
    try:
        with open(os.path.join(work, spec["file"]), "w", encoding="utf-8") as f:
            f.write(code)
        env = {"PATH": os.environ.get("PATH", ""), "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
               "PYTHONIOENCODING": "utf-8"}
        if spec.get("build"):
            b = subprocess.run(spec["build"], cwd=work, capture_output=True, text=True, timeout=30, env=env)
            if b.returncode != 0:
                return {"results": [], "passed": 0, "total": len(tests), "verdict": "COMPILATION_ERROR",
                        "compile_error": b.stderr[-3000:]}
        run_cmd = list(spec["run"])
        if run_cmd[0] == "./main" and os.name == "nt":
            run_cmd[0] = os.path.join(work, "main.exe")
        results, passed = [], 0
        for i, t in enumerate(tests, 1):
            try:
                p = subprocess.run(run_cmd, cwd=work, input=t.get("input", ""), capture_output=True, text=True,
                                   timeout=timeout, env=env)
                out, err = p.stdout, p.stderr
                status = "PASS" if p.returncode == 0 and _norm(out) == _norm(t.get("output")) else \
                    ("RUNTIME_ERROR" if p.returncode != 0 else "WRONG_ANSWER")
            except subprocess.TimeoutExpired:
                out, err, status = "", f"Time limit exceeded ({timeout}s)", "TIME_LIMIT"
            passed += status == "PASS"
            results.append({"n": i, "input": t.get("input", ""), "expected": t.get("output", ""),
                            "actual": (out or "")[:2000], "error": (err or "")[-1500:], "status": status})
        verdict = "ACCEPTED" if passed == len(tests) else ("PARTIAL" if passed else results[0]["status"] if results else "NO_TESTS")
        return {"results": results, "passed": passed, "total": len(tests), "verdict": verdict, "compile_error": ""}
    finally:
        shutil.rmtree(work, ignore_errors=True)
