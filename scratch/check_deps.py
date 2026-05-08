import os
import re

def get_all_imports(directory):
    imports = set()
    for root, dirs, files in os.walk(directory):
        for file in files:
            if file.endswith(".py"):
                path = os.path.join(root, file)
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        for line in f:
                            match = re.match(r"^\s*(?:import|from)\s+([a-zA-Z0-9_]+)", line)
                            if match:
                                imports.add(match.group(1))
                except Exception:
                    pass
    return imports

if __name__ == "__main__":
    all_imports = get_all_imports(".")
    print(f"Detected imports: {sorted(list(all_imports))}")
