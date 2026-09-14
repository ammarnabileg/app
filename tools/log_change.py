import os
import datetime
import subprocess

CHANGELOG_FILE = 'CHANGELOG.md'
LOG_FILE = 'tools/change_buffer.txt'  # Temp file for current working session

def get_modified_files():
    """Get list of modified files using git status."""
    try:
        # Check for modified, new, or deleted files
        result = subprocess.run(['git', 'status', '--porcelain'], capture_output=True, text=True)
        files = []
        for line in result.stdout.splitlines():
            if line.strip():
                # Format: " M filename" or "?? filename"
                parts = line.split(maxsplit=1)
                if len(parts) > 1:
                    files.append(parts[1])
        return files
    except Exception:
        # If git is not installed or initialized, return empty
        return []

def read_changelog():
    if not os.path.exists(CHANGELOG_FILE):
        return []
    with open(CHANGELOG_FILE, 'r', encoding='utf-8') as f:
        return f.readlines()

def write_changelog(lines):
    with open(CHANGELOG_FILE, 'w', encoding='utf-8') as f:
        f.writelines(lines)

def log_change():
    print("Locked & Loaded: Change Logger v1.0 🚀")
    print("-" * 40)
    
    # Auto-detect modified files
    mod_files = get_modified_files()
    files_str = ""
    if mod_files:
        print(f"Detected modified files: {', '.join(mod_files)}")
        files_str = f" [files: {', '.join(mod_files)}]"
    else:
        print("No dirty files detected via git (Is git initialized?).")
    
    # Get user input
    desc = input("\nDescribe your change (e.g. 'Fix login bug'): ").strip()
    if not desc:
        print("No description provided. Aborting.")
        return

    # Prepare line
    # Format: 1) Description [files: ...]
    new_entry = f"{desc}{files_str}"
    
    # Read existing
    lines = read_changelog()
    
    # Find insertion point: under the first version header
    # We look for the first line starting with 'v' and then the next line starting with '1)' or number
    # Actually, simplistic approach: Find the first non-empty line after the first header.
    
    # Better: Scan for the first version header "vX.X"
    # Then insert at top of that list (renumbering?) or append to end of that list?
    # User example:
    # v6.2 ...
    # 1) ...
    # 2) ...
    
    # Algorithm:
    # 1. Find index of first version line (starts with 'v')
    # 2. Find the last item number in that block to increment it.
    
    insert_idx = -1
    last_num = 0
    
    for i, line in enumerate(lines):
        if line.startswith('v'):
            # Found version header, e.g. "v6.2 (27 May...)"
            # Search downwards for the last item associated with this version
            # The block ends when we hit another empty line or another 'v'
            j = i + 1
            while j < len(lines):
                subline = lines[j].strip()
                if not subline:
                    # Empty line, end of block? Maybe.
                    # Verify if next lines are blank too or new version
                    pass
                elif subline.startswith('v'):
                    # Next version started, break
                    break
                elif ')' in subline:
                    # It's an item "1) ..."
                    try:
                        num = int(subline.split(')')[0])
                        if num > last_num:
                            last_num = num
                        insert_idx = j + 1 # Insert after this line
                    except:
                        pass
                j += 1
            
            if insert_idx == -1:
                # No items yet for this version, insert after header
                insert_idx = i + 1
            break
            
    if insert_idx == -1:
        # No version found? Prepend to file?
        print("Error: No version header found in CHANGELOG.md. Please create a version first.")
        return

    # Check if we need to add a newline if insert_idx points to a version header or non-item
    # Basically we want to append.
    
    final_entry = f"{last_num + 1}) {new_entry}\n"
    
    lines.insert(insert_idx, final_entry)
    
    # Write back
    write_changelog(lines)
    print(f"\n✅ Logged: {final_entry.strip()}")
    print(f"Updated {CHANGELOG_FILE}")

if __name__ == "__main__":
    try:
        log_change()
        # Optional: Auto-add to git
        # subprocess.run(['git', 'add', CHANGELOG_FILE])
    except KeyboardInterrupt:
        print("\nCancelled.")
