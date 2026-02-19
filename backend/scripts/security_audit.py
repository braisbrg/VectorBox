
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

    # 2. Normalize versions
    clean_packages = []
    
    print("Analyzing package versions...")
    normalization_count = 0
    
    for pkg in installed_packages:
        # filtering out editable installs or strange paths if necessary, 
        # but pip-audit usually wants name==version
        if "==" in pkg:
            name, version = pkg.split("==", 1)
            # Check for local version identifiers (e.g., 2.5.1+cpu)
            if "+" in version:
                clean_version = version.split("+")[0]
                clean_pkg = f"{name}=={clean_version}"
                print(f"  [NORMALIZE] {pkg} -> {clean_pkg}")
                clean_packages.append(clean_pkg)
                normalization_count += 1
                continue
        
        # If no normalization needed, keep original
        clean_packages.append(pkg)

    print(f"Normalization complete. Modified {normalization_count} packages.")

    # 3. Write to temp file
    temp_req_file = "temp_audit_reqs.txt"
    try:
        with open(temp_req_file, "w") as f:
            f.write("\n".join(clean_packages))
    except IOError as e:
        print(f"Error writing temp file: {e}")
        sys.exit(1)

    # 4. Run pip-audit
    print(f"\nRunning pip-audit against normalized list...")
    print("-" * 50)
    
    exit_code = 0
    try:
        # We pass through stdout/stderr so the user sees the real report
        # --strict fails on any vulnerability
        # --progress-spinner off to keep logs clean in CI/Docker
        audit_cmd = ["pip-audit", "-r", temp_req_file, "--strict", "--progress-spinner", "off"]
        
        # Run and wait
        process = subprocess.run(audit_cmd)
        exit_code = process.returncode

    except Exception as e:
        print(f"FATAL: Error running pip-audit: {e}")
        exit_code = 1
    finally:
        # Cleanup
        if os.path.exists(temp_req_file):
            os.remove(temp_req_file)
            
    print("-" * 50)
    if exit_code == 0:
        print("Security Audit Passed! No known vulnerabilities found.")
    else:
        print(f"Security Audit Failed! (Exit Code: {exit_code})")
        
    sys.exit(exit_code)

if __name__ == "__main__":
    main()
