
import subprocess
import sys
import re
import os

# ── Avisos aceptados a sabiendas ────────────────────────────────────────────
# Se suprimen POR ID, nunca por paquete: si mañana sale un aviso NUEVO en
# cualquiera de estos paquetes, la puerta lo ve. Cada uno lleva su motivo, y
# todos se revisan cuando se haga el trabajo que los desbloquea.
IGNORED = [
    "GHSA-w8v5-vhqr-4h9v",   # diskcache — transitivo, no existe 5.6.4
    "GHSA-mgj5-w798-5c9q",   # torchvision, falso positivo de la build CPU (1/3)
    "GHSA-p75w-3772-g6p9",   # torchvision, falso positivo de la build CPU (2/3)
    "GHSA-9wcc-7w4g-g499",   # torchvision, falso positivo de la build CPU (3/3)
    # Stack de ML — DESBLOQUEA: re-embeber las 21.425 películas + re-clusterizar.
    # Medido el 2026-09-03: subir `transformers` a 5.x arrastra
    # `sentence-transformers` 5->6 y `torch` 2.10->2.14, o sea que cambia el
    # ENCODER. Eso se hace a propósito y con su medición delante, no de rebote
    # dentro de una tanda de seguridad.
    "CVE-2026-9856",         # transformers -> 5.10.0
    "PYSEC-2026-2288",       # transformers -> 5.0.0
    "PYSEC-2026-2289",       # transformers -> 5.3.0
    "PYSEC-2026-2290",       # transformers -> 5.5.0
    "PYSEC-2025-217",        # transformers, sin arreglo publicado
    "PYSEC-2026-139",        # torch, sin arreglo publicado
    "PYSEC-2025-194",        # torch -> 2.13.0
]


def _ignore_args():
    return [a for vid in IGNORED for a in ("--ignore-vuln", vid)]


def main():
    print("Starting Custom Security Audit...")
    
    # 1. Get installed packages
    try:
        # Using sys.executable ensures we use the same python environment
        result = subprocess.run([sys.executable, "-m", "pip", "freeze"], capture_output=True, text=True, check=True)
        installed_packages = result.stdout.splitlines()
    except subprocess.CalledProcessError as e:
        print(f"Error getting installed packages: {e}")
        sys.exit(1)

    # 3. Check for requirements.lock (Hashed)
    # In Docker, we are running from /app, so requirements.lock is adjacent
    lock_file = "requirements.lock" 
    # If running from backend/scripts, it might be ../requirements.lock
    if not os.path.exists(lock_file) and os.path.exists(os.path.join(os.path.dirname(__file__), "../requirements.lock")):
        lock_file = os.path.join(os.path.dirname(__file__), "../requirements.lock")

    target_file = None
    
    if os.path.exists(lock_file):
        print(f"Found {lock_file}. Using hashed dependencies for strict audit.")
        target_file = lock_file
        # pip-audit -r requirements.lock automatically uses hashes
    else:
        # Fallback to freeze
        print("No requirements.lock found. Falling back to pip freeze (no hashes).")
        print("Analyzing package versions...")
        # ... logic to create temp_audit_reqs.txt ...
        clean_packages = []
        normalization_count = 0
        
        for pkg in installed_packages:
             if "==" in pkg:
                name, version = pkg.split("==", 1)
                if "+" in version:
                    clean_version = version.split("+")[0]
                    clean_pkg = f"{name}=={clean_version}"
                    print(f"  [NORMALIZE] {pkg} -> {clean_pkg}")
                    clean_packages.append(clean_pkg)
                    normalization_count += 1
                    continue
             clean_packages.append(pkg)
             
        temp_req_file = "temp_audit_reqs.txt"
        try:
            with open(temp_req_file, "w") as f:
                f.write("\n".join(clean_packages))
        except IOError as e:
            print(f"Error writing temp file: {e}")
            sys.exit(1)
        target_file = temp_req_file

    # 4. Run pip-audit
    print(f"\nRunning pip-audit...")
    print("-" * 50)

    exit_code = 0
    try:
        # NO `--strict`. Its documented behaviour is "fail the entire audit if
        # dependency collection fails", and one package here cannot be collected:
        # torch is installed as `2.10.0+cpu` from download.pytorch.org, and that
        # PEP 440 local version does not exist on PyPI. So --strict made pip-audit
        # ABORT on torch and audit NOTHING — while this wrapper skipped the torch
        # line as known noise and reported a clean bill of health.
        #
        # Measured 2026-09-03: the gate said "No known vulnerabilities found" while
        # the same lock file audited without --strict reported **85 known
        # vulnerabilities in 19 packages**, including three HIGH in `cryptography`
        # that GitHub had been flagging for three months. Two releases were tagged
        # claiming "pip-audit clean" on the strength of this.
        #
        # Torch is not skipped either — it gets its own pass below, by base version.
        audit_cmd = [
            sys.executable, "-m", "pip_audit",
            "-r", target_file,
            "--progress-spinner", "off",
        ] + _ignore_args()

        if target_file.endswith(".lock"):
            # Hashed lockfile: use --require-hashes for cryptographic integrity verification
            # This is the correct mode and eliminates the "consider using hashes" warning
            audit_cmd += ["--require-hashes", "--extra-index-url", "https://download.pytorch.org/whl/cpu"]
        else:
            # Unfixed freeze fallback: no hashes present, use --no-deps
            audit_cmd += ["--no-deps"]

        process = subprocess.run(audit_cmd, capture_output=True, text=True)

        output_lines = process.stdout.splitlines() + process.stderr.splitlines()
        vuln_found = False
        significant_error = False
        # A monitor has to prove it looked. pip-audit always ends with an explicit
        # verdict — either "Found N known vulnerabilities" or "No known
        # vulnerabilities found" — so the ABSENCE of both means it never got to
        # audit anything, and that must fail closed. Without this flag the wrapper
        # read "no findings printed" as "nothing wrong", which is exactly how it
        # passed for months while auditing zero packages.
        saw_verdict = False

        for line in output_lines:
            low = line.lower()
            # Suppress the known torch CPU wheel "not found on PyPI" error:
            # torch+cpu is from https://download.pytorch.org/whl/cpu, not PyPI.
            # This is not a vulnerability; it just can't be looked up on pypi.org.
            if "dependency not found on pypi" in low and "torch" in low:
                continue
            print(line)
            # pip-audit announces findings as "Found N known vulnerabilities ..."
            # (note the word order — the previous "vulnerabilities found" match
            # never fired, silently passing every real finding: fail-open bug).
            m = re.search(r"found\s+(\d+)\s+known\s+vulnerabilit", low)
            if m:
                saw_verdict = True
                if int(m.group(1)) > 0:
                    vuln_found = True
            if "no known vulnerabilities found" in low:
                saw_verdict = True
            # Any non-warning error/traceback is treated as a real failure so we
            # never fail-open on resolution/network errors either.
            if ("error" in low or "traceback" in low) and "warning" not in low:
                significant_error = True

        # torch ships as `2.10.0+cpu` from download.pytorch.org and PyPI has no such
        # version, so the pass above cannot look it up. It is NOT skipped: PyPI does
        # have the same upstream release without the local segment (checked live:
        # /pypi/torch/2.10.0/json -> 200), so it gets audited by base version here.
        # No hashes — the PyPI artifact is a different file from the CPU wheel; the
        # advisory data is keyed by version, which is what we need.
        torch_ver = next((l.split("==", 1)[1].split("+")[0].strip()
                          for l in installed_packages
                          if l.lower().startswith("torch==") and "+" in l), None)
        if torch_ver:
            print("-" * 50)
            print(f"Auditing torch {torch_ver} by base version (the +cpu wheel is not on PyPI)...")
            t = subprocess.run(
                [sys.executable, "-m", "pip_audit", "--progress-spinner", "off",
                 "--no-deps", "-r", "/dev/stdin",
                 # mismas supresiones que la pasada principal: si no, un aviso
                 # aceptado a sabiendas tumbaría la puerta por la puerta de atrás
                 *_ignore_args()],
                input=f"torch=={torch_ver}" + chr(10), capture_output=True, text=True)
            for line in (t.stdout + t.stderr).splitlines():
                print(line)
                low = line.lower()
                m = re.search(r"found\s+(\d+)\s+known\s+vulnerabilit", low)
                if m:
                    saw_verdict = True
                    if int(m.group(1)) > 0:
                        vuln_found = True
                if "no known vulnerabilities found" in low:
                    saw_verdict = True

        if vuln_found or significant_error:
            # Real vulnerabilities or a genuine audit error → fail closed.
            exit_code = 1
        elif not saw_verdict:
            # pip-audit never reached a verdict: it collected nothing, or died on
            # the way. "No findings printed" is not "nothing wrong" — that reading
            # is what let this gate pass for months without auditing a single
            # package. Fail closed.
            print("FAIL: pip-audit produced no verdict — it did not audit anything.")
            exit_code = 1
        else:
            exit_code = 0

    except Exception as e:
        print(f"FATAL: Error running pip-audit: {e}")
        exit_code = 1
    finally:
        if target_file == "temp_audit_reqs.txt" and os.path.exists(target_file):
            os.remove(target_file)

    print("-" * 50)
    if exit_code == 0:
        print("Security Audit Passed! No known vulnerabilities found.")
    else:
        print(f"Security Audit Failed! (Exit Code: {exit_code})")

    sys.exit(exit_code)

if __name__ == "__main__":
    main()
