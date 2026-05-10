# small_test_generator.py
# Creates a folder named "test" and fills it with a few sample files.

from pathlib import Path

# Create the folder
test_folder = Path("test")
test_folder.mkdir(exist_ok=True)

# Files to generate
files = {
    "readme.txt": "This is a sample text file.\n",
    "data.json": '{ "name": "Test", "value": 123 }\n',
    "script.py": 'print("Hello from generated Python file!")\n',
    "notes.md": "# Test Folder\n\nThis is a markdown file.\n",
    "config.ini": "[settings]\nmode=test\n",
    "index.html": "<html><body><h1>Test Page</h1></body></html>\n",
}

# Create each file
for filename, content in files.items():
    file_path = test_folder / filename
    file_path.write_text(content)

print(f"Created folder '{test_folder}' with {len(files)} files.")