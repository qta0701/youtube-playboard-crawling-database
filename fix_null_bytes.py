
import os

file_path = 'dashboard_app.py'

try:
    with open(file_path, 'rb') as f:
        content = f.read()
    
    # Remove null bytes
    cleaned_content = content.replace(b'\x00', b'')
    
    # Write back
    with open(file_path, 'wb') as f:
        f.write(cleaned_content)
        
    print(f"Successfully cleaned {file_path}. Original size: {len(content)}, New size: {len(cleaned_content)}")

except Exception as e:
    print(f"Error: {e}")
