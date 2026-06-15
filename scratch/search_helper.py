import sys

def search_in_file(filepath, query):
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            lines = f.readlines()
    except UnicodeDecodeError:
        with open(filepath, 'r', encoding='utf-8-sig') as f:
            lines = f.readlines()
            
    found = False
    for i, line in enumerate(lines, 1):
        if query.lower() in line.lower():
            print(f"Line {i}: {line.strip()}")
            found = True
    if not found:
        print("No matches found.")

if __name__ == '__main__':
    search_in_file('app.py', sys.argv[1] if len(sys.argv) > 1 else 'copy')
