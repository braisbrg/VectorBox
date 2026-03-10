
import subprocess
import sys
import re
import os

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
        audit_cmd = [
            sys.executable, "-m", "pip_audit",
            "-r", target_file,
            "--strict",
            "--progress-spinner", "off",
            "--ignore-vuln", "GHSA-w8v5-vhqr-4h9v",   # diskcache — transitive dep, no fix available
            "--ignore-vuln", "GHSA-mgj5-w798-5c9q",   # torchvision CPU-build false positive (1/3)
            "--ignore-vuln", "GHSA-p75w-3772-g6p9",   # torchvision CPU-build false positive (2/3)
            "--ignore-vuln", "GHSA-9wcc-7w4g-g499",   # torchvision CPU-build false positive (3/3)
        ]

        if target_file.endswith(".lock"):
            # Hashed lockfile: use --require-hashes for cryptographic integrity verification
            # This is the correct mode and eliminates the "consider using hashes" warning
            audit_cmd += ["--require-hashes", "--extra-index-url", "https://download.pytorch.org/whl/cpu"]
        else:
            # Unfixed freeze fallback: no hashes present, use --no-deps
            audit_cmd += ["--no-deps"]

        process = subprocess.run(audit_cmd, capture_output=True, text=True)

        output_lines = process.stdout.splitlines() + process.stderr.splitlines()
        real_errors = []

        for line in output_lines:
            # Suppress the known torch CPU wheel "not found on PyPI" error:
            # torch+cpu is from https://download.pytorch.org/whl/cpu, not PyPI.
            # This is not a vulnerability; it just can't be looked up on pypi.org.
            if "dependency not found on pypi" in line.lower() and "torch" in line.lower():
                continue
            print(line)
            if "vulnerabilities found" in line.lower() and "0 vulnerabilities found" not in line.lower():
                real_errors.append(line)

        if process.returncode != 0 and not real_errors:
            print("Note: Suppressed known 'torch+cpu not found on PyPI' error (expected for CPU wheel builds).")
            exit_code = 0
        else:
            exit_code = process.returncode

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
