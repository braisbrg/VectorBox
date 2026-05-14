import ast
import os
import sys

def find_await_in_loops(filepath):
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            tree = ast.parse(f.read(), filename=filepath)
    except Exception as e:
        return []
    
    findings = []
    
    for node in ast.walk(tree):
        if isinstance(node, (ast.For, ast.AsyncFor, ast.While)):
            for subnode in ast.walk(node):
                if isinstance(subnode, ast.Await):
                    # We found an await inside a loop!
                    # Let's extract the function call if it is one
                    if isinstance(subnode.value, ast.Call):
                        call_func = subnode.value.func
                        func_name = ""
                        if isinstance(call_func, ast.Attribute):
                            if isinstance(call_func.value, ast.Name):
                                func_name = f"{call_func.value.id}.{call_func.attr}"
                            else:
                                func_name = f"*.{call_func.attr}"
                        elif isinstance(call_func, ast.Name):
                            func_name = call_func.id
                        
                        # Filter out some common benign awaits like asyncio.sleep
                        if func_name not in ["asyncio.sleep"]:
                            findings.append({
                                'line': subnode.lineno,
                                'call': func_name
                            })
    return findings

if __name__ == "__main__":
    count = 0
    for root, dirs, files in os.walk('backend'):
        # skip venv or tests
        if 'venv' in root or 'tests' in root or '__pycache__' in root:
            continue
        for file in files:
            if file.endswith('.py'):
                filepath = os.path.join(root, file)
                issues = find_await_in_loops(filepath)
                if issues:
                    print(f"File: {filepath}")
                    for issue in issues:
                        print(f"  Line {issue['line']}: await {issue['call']}")
                    count += len(issues)
    
    if count == 0:
        print("No N+1 queries found!")
    else:
        print(f"Total N+1 potential issues found: {count}")
