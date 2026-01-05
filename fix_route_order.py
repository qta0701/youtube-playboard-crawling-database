
import os

file_path = 'dashboard_app.py'

try:
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    main_marker = "if __name__ == '__main__':"
    route_marker = "@app.route('/api/export/csv')"
    
    main_idx = content.find(main_marker)
    route_idx = content.find(route_marker)
    
    if main_idx != -1 and route_idx != -1 and route_idx > main_idx:
        print("Route found after main block. Moving it.")
        
        # Split into three parts:
        # 1. Everything before main
        # 2. Main block (up to route) -> actually route is after main, so main block is between main_marker and route_marker?
        #    No, Main block starts at main_marker. Route is somewhere after.
        #    Typically route is appended at the very end.
        
        # Let's verify if route is at the very end.
        # Everything from route_idx to end is the function.
        
        route_code = content[route_idx:]
        
        # content before main
        before_main = content[:main_idx]
        
        # content of main block (excluding the route which is at the end)
        # We need to be careful about what's between main execution and the route.
        # It's likely just the app.run() lines.
        
        # The part between main_marker and route_marker
        main_block_content = content[main_idx:route_idx]
        
        # New structure:
        # [Before Main]
        # [Route Code]
        # [Main Block]
        
        new_content = before_main + "\n" + route_code + "\n\n" + main_block_content
        
        # Clean up possible extra newlines at the end of main_block_content if any
        # main_block_content probably ends with newlines before the route started.
        
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(new_content)
            
        print("Successfully moved api_export_csv before main block.")
        
    else:
        print("Route not found or already in correct position.")
        print(f"Main index: {main_idx}, Route index: {route_idx}")

except Exception as e:
    print(f"Error: {e}")
