
import curses
import subprocess
import os
import time
from dataclasses import dataclass, field
from typing import List, Optional

# --- Data Structures ---

@dataclass
class AppState:
    """Encapsulates the entire state of the application."""
    input_mode: str = 'search'  # 'search', 'command', or 'select'
    input_buffer: str = ""
    last_search_query: str = ""
    
    raw_results: List[str] = field(default_factory=list)
    filtered_results: List[str] = field(default_factory=list)
    
    scroll_pos: int = 0
    selected_index: int = -1
    
    # Settings
    result_limit: int = 50
    delay_limit: int = 1000
    search_delay_ms: int = 1000
    
    # Filtering
    filter_dir: str = ""
    blacklist: List[str] = field(default_factory=list)
    undo_stack: List[str] = field(default_factory=list)
    
    # UI State
    status_message: str = ""
    status_message_expiry: float = 0
    pending_search: bool = False
    last_key_press_time: float = 0.0
    
    def set_status(self, message: str, duration: int = 2):
        """Set a temporary status message."""
        self.status_message = message
        self.status_message_expiry = time.time() + duration

# --- Core Logic ---

def run_locate_command(query: str, limit: int) -> List[str]:
    """Executes the 'locate' command and returns the results."""
    if not query:
        return []
    command = ['locate', '-i', '-l', str(limit), query]
    try:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        stdout, stderr = process.communicate()
        if process.returncode == 0:
            return stdout.strip().split('\n') if stdout.strip() else []
        return [f"Error: {stderr.strip()}"]
    except FileNotFoundError:
        return ["Error: 'locate' command not found. Please ensure it's installed."]
    except Exception as e:
        return [f"An unexpected error occurred: {e}"]

def update_filtered_results(state: AppState):
    """Applies directory and blacklist filters to the raw results."""
    current_results = state.raw_results
    if state.filter_dir:
        current_results = [r for r in current_results if r.startswith(state.filter_dir)]
    if state.blacklist:
        current_results = [r for r in current_results if not any(r.startswith(b) for b in state.blacklist)]
    state.filtered_results = current_results
    if state.selected_index >= len(state.filtered_results):
        state.selected_index = max(0, len(state.filtered_results) - 1)

# --- UI and Interaction ---

def setup_colors():
    """Initializes color pairs for the TUI."""
    curses.start_color()
    curses.init_pair(1, curses.COLOR_BLACK, curses.COLOR_WHITE)
    curses.init_pair(2, curses.COLOR_CYAN, curses.COLOR_BLACK)
    curses.init_pair(3, curses.COLOR_BLACK, curses.COLOR_CYAN)

def draw_ui(stdscr, state: AppState):
    """Draws all UI components onto the screen."""
    height, width = stdscr.getmaxyx()
    stdscr.clear()

    # Define bar_prefix early for use in drawing and cursor positioning
    bar_prefix = "Search: " if state.input_mode == 'search' else "Command: "

    # 1. Input Bar
    stdscr.attron(curses.color_pair(1))
    stdscr.addstr(0, 0, " " * (width - 1))
    if state.input_mode == 'select':
        display_text = f"SELECT MODE (Query: {state.last_search_query})"
        stdscr.addstr(0, 0, display_text[:width-1])
    else:
        stdscr.addstr(0, 0, f"{bar_prefix}{state.input_buffer}")
    stdscr.attroff(curses.color_pair(1))

    # 2. Results Pane
    results_to_display = state.filtered_results[state.scroll_pos:state.scroll_pos + height - 2]
    for i, line in enumerate(results_to_display, start=1):
        line_display = line[:width - 1] + '…' if len(line) > width else line
        current_index = state.scroll_pos + i - 1
        color = curses.color_pair(3) if current_index == state.selected_index else curses.color_pair(2)
        stdscr.addstr(i, 0, line_display, color)

    # 3. Status Bar
    if state.status_message and time.time() < state.status_message_expiry:
        status_text = state.status_message
    elif state.input_mode == 'search':
        status_text = "MODE: SEARCH | Press '/' for commands or Enter to select"
    elif state.input_mode == 'command':
        status_text = "MODE: COMMAND | Enter to execute, ESC to cancel"
    else:  # 'select' mode
        status_text = "j/k: Nav | c: Copy | b: Blacklist | u: Undo | o: Open with | f: Filter | /: Cmd | Enter: Open | ESC: Search"
    
    stdscr.addstr(height - 1, 0, " " * (width - 1))
    stdscr.addstr(height - 1, 0, status_text[:width - 1])

    # 4. Cursor Position
    if state.input_mode in ['search', 'command']:
        curses.curs_set(1)
        stdscr.move(0, len(bar_prefix) + len(state.input_buffer))
    else:
        curses.curs_set(0)
    
    stdscr.refresh()

# --- Input Handlers for different modes ---

def handle_search_mode(key: int, state: AppState) -> bool:
    """Handles input when in SEARCH mode. Returns True if a search should be triggered."""
    buffer_modified = False
    if key in (curses.KEY_BACKSPACE, 127, 8):
        state.input_buffer = state.input_buffer[:-1]
        buffer_modified = True
    elif key in (curses.KEY_ENTER, 10, 13):
        if state.filtered_results:
            state.input_mode = 'select'
            if state.selected_index == -1: state.selected_index = 0
    elif key == ord('/'):
        state.input_mode = 'command'
        state.input_buffer = "/"
    elif 32 <= key <= 126:
        state.input_buffer += chr(key)
        buffer_modified = True
    
    if buffer_modified:
        state.last_search_query = state.input_buffer
        state.selected_index = -1
        if state.result_limit > state.delay_limit:
            state.pending_search = True
            state.last_key_press_time = time.time()
        else:
            return True # Trigger immediate search
    return False

def handle_select_mode(stdscr, key: int, state: AppState) -> bool:
    """Handles input in SELECT mode. Returns True if results need refiltering."""
    refilter_needed = False
    selected_path = state.filtered_results[state.selected_index] if 0 <= state.selected_index < len(state.filtered_results) else None

    if key == ord('j'):
        if state.filtered_results: state.selected_index = min(len(state.filtered_results) - 1, state.selected_index + 1)
    elif key == ord('k'):
        if state.filtered_results: state.selected_index = max(0, state.selected_index - 1)
    elif key in (curses.KEY_ENTER, 10, 13) and selected_path:
        open_file(selected_path)
    elif key == 27: # ESC
        state.input_mode = 'search'
        state.selected_index = -1
    elif key == ord('/') :
        state.input_mode = 'command'
        state.input_buffer = "/"
    elif key == ord('b') and selected_path:
        dir_to_blacklist = os.path.dirname(selected_path)
        if dir_to_blacklist and dir_to_blacklist not in state.blacklist:
            state.blacklist.append(dir_to_blacklist)
            state.undo_stack.append(dir_to_blacklist)
            refilter_needed = True
    elif key == ord('u'):
        if state.undo_stack:
            last_blacklisted = state.undo_stack.pop()
            if last_blacklisted in state.blacklist: state.blacklist.remove(last_blacklisted)
            refilter_needed = True
    elif key == ord('c') and selected_path:
        if copy_to_clipboard(selected_path):
            state.set_status(f"Copied: {os.path.basename(selected_path)}")
        else:
            state.set_status("Error: Failed to copy. Is 'wl-clipboard' installed?")
    elif key == ord('o') and selected_path:
        cmd_str = get_user_input(stdscr, f"Open '{os.path.basename(selected_path)}' with: ")
        if cmd_str:
            try:
                subprocess.Popen(cmd_str.split() + [selected_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                state.set_status(f"Opening with '{cmd_str}'...")
            except Exception as e:
                state.set_status(f"Failed to run: {e}", 3)
    elif key == ord('f'):
        cmd_str = get_user_input(stdscr, "Filter results with command: ")
        if cmd_str and state.filtered_results:
            new_results, error = run_filter_command(cmd_str, state.filtered_results)
            if error:
                state.set_status(f"Filter error: {error}", 4)
            else:
                state.filtered_results = new_results
                state.selected_index = 0 if new_results else -1
                state.scroll_pos = 0
                state.set_status("Results filtered.")

    return refilter_needed

def handle_command_mode(stdscr, key: int, state: AppState) -> tuple[bool, bool]:
    """Handles input in COMMAND mode. Returns (trigger_search, refilter_needed)."""
    trigger_search, refilter_needed = False, False
    if key in (curses.KEY_ENTER, 10, 13):
        command_str = state.input_buffer[1:] # remove leading '/'
        is_known = True
        
        if command_str.startswith("set result="):
            try: state.result_limit = int(command_str.split("=")[1])
            except: pass
        elif command_str.startswith("set delaylimit="):
            try: state.delay_limit = int(command_str.split("=")[1])
            except: pass
        elif command_str.startswith("set delay="):
            try: state.search_delay_ms = int(command_str.split("=")[1])
            except: pass
        elif command_str.startswith("dir "):
            path = command_str[4:].strip()
            state.filter_dir = os.path.abspath(os.path.expanduser(path)) if path else ""
            refilter_needed = True
        elif command_str.startswith("black list"):
            state.blacklist = blacklist_manager_view(stdscr, state.blacklist)
            refilter_needed = True
        elif command_str.startswith("black add "):
            path = command_str[10:].strip()
            if path:
                dir_to_add = os.path.abspath(os.path.expanduser(path))
                if dir_to_add not in state.blacklist: state.blacklist.append(dir_to_add)
            refilter_needed = True
        else:
            is_known = False

        state.input_mode = 'search'
        if is_known:
            state.input_buffer = state.last_search_query
        else: # Unrecognized command becomes a search
            # FIX: Strip the '/' and search for the command text itself, updating the buffer.
            unrecognized_search = state.input_buffer.lstrip('/')
            state.last_search_query = unrecognized_search
            state.input_buffer = unrecognized_search
            trigger_search = True

    elif key == 27: # ESC
        state.input_mode = 'search'
        state.input_buffer = state.last_search_query
    elif key in (curses.KEY_BACKSPACE, 127, 8):
        if len(state.input_buffer) > 1: state.input_buffer = state.input_buffer[:-1]
        else: 
            state.input_mode = 'search'
            state.input_buffer = state.last_search_query
    elif 32 <= key <= 126:
        state.input_buffer += chr(key)
        
    return trigger_search, refilter_needed

# --- Helper functions from original main ---
# (These remain largely unchanged and are here for completeness)

def open_file(filepath: str):
    try:
        subprocess.Popen(['xdg-open', filepath], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (FileNotFoundError, Exception):
        pass

def copy_to_clipboard(text: str) -> bool:
    if not text: return False
    try:
        p = subprocess.Popen(['wl-copy'], stdin=subprocess.PIPE, text=True)
        p.communicate(input=text)
        return True
    except (FileNotFoundError, Exception):
        return False

def get_user_input(stdscr, prompt: str) -> Optional[str]:
    curses.curs_set(1)
    input_buffer = ""
    height, width = stdscr.getmaxyx()
    while True:
        stdscr.attron(curses.color_pair(1))
        stdscr.addstr(0, 0, " " * (width - 1))
        full_prompt = f"{prompt}{input_buffer}"
        stdscr.addstr(0, 0, full_prompt[:width-1])
        stdscr.attroff(curses.color_pair(1))
        stdscr.move(0, len(full_prompt))
        stdscr.refresh()
        key = stdscr.getch()
        if key in (curses.KEY_ENTER, 10, 13): return input_buffer
        elif key == 27: return None
        elif key in (curses.KEY_BACKSPACE, 127, 8): input_buffer = input_buffer[:-1]
        elif 32 <= key <= 126: input_buffer += chr(key)

def run_filter_command(command: str, input_data: List[str]) -> tuple[Optional[List[str]], Optional[str]]:
    input_str = "\n".join(input_data)
    try:
        process = subprocess.Popen(command, shell=True, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        stdout, stderr = process.communicate(input=input_str)
        if process.returncode == 0:
            return stdout.strip().split('\n') if stdout.strip() else [], None
        return None, stderr.strip()
    except Exception as e:
        return None, str(e)

def blacklist_manager_view(stdscr, blacklist: List[str]) -> List[str]:
    selected_index, scroll_pos = 0, 0
    curses.curs_set(0)
    while True:
        height, width = stdscr.getmaxyx()
        stdscr.clear()
        visible_height = height - 3
        
        title = "Blacklist Manager"
        stdscr.addstr(0, (width - len(title)) // 2, title, curses.A_BOLD)
        
        items_to_display = blacklist[scroll_pos:scroll_pos + visible_height]
        for i, item in enumerate(items_to_display):
            current_index = scroll_pos + i
            display_item = f"{current_index + 1}. {item}"
            if len(display_item) > width - 2: display_item = display_item[:width - 3] + "…"
            color = curses.color_pair(3) if current_index == selected_index else curses.A_NORMAL
            stdscr.addstr(i + 2, 1, display_item, color)

        instructions = "j/k: Navigate | d: Delete | q: Back to Search"
        stdscr.addstr(height - 1, 0, " " * (width - 1), curses.color_pair(1))
        stdscr.addstr(height - 1, 1, instructions, curses.color_pair(1))
        stdscr.refresh()

        key = stdscr.getch()
        if key == ord('q'): break
        elif key == ord('j'):
            if blacklist: selected_index = min(len(blacklist) - 1, selected_index + 1)
        elif key == ord('k'):
            if blacklist: selected_index = max(0, selected_index - 1)
        elif key == ord('d'):
            if blacklist and 0 <= selected_index < len(blacklist):
                blacklist.pop(selected_index)
                if selected_index >= len(blacklist) and blacklist: selected_index = len(blacklist) - 1
        
        if visible_height > 0:
            if selected_index < scroll_pos: scroll_pos = selected_index
            if selected_index >= scroll_pos + visible_height: scroll_pos = selected_index - visible_height + 1
    
    curses.curs_set(1)
    return blacklist

# --- Main Application Loop ---

def main_loop(stdscr):
    """The refactored main application loop."""
    # Initialization
    stdscr.nodelay(True)
    stdscr.timeout(100)
    setup_colors()
    state = AppState()

    while True:
        # 1. Check for and trigger delayed search
        trigger_search = False
        if state.pending_search and (time.time() - state.last_key_press_time) * 1000 >= state.search_delay_ms:
            trigger_search = True
            state.pending_search = False
        
        # 2. Draw the current state of the UI
        draw_ui(stdscr, state)
        
        # 3. Get user input
        try:
            key = stdscr.getch()
        except KeyboardInterrupt:
            break
        
        if key == -1 and not trigger_search:
            continue
            
        # 4. Process input based on the current mode
        refilter_needed = False
        if state.input_mode == 'search':
            if handle_search_mode(key, state):
                trigger_search = True
        elif state.input_mode == 'select':
            if handle_select_mode(stdscr, key, state):
                refilter_needed = True
        elif state.input_mode == 'command':
            # FIX: Pass stdscr to the handler to prevent crash on 'black list'
            search_now, filter_now = handle_command_mode(stdscr, key, state)
            if search_now: trigger_search = True
            if filter_now: refilter_needed = True

        # 5. Update results if a search was triggered
        if trigger_search:
            state.scroll_pos = 0
            state.raw_results = run_locate_command(state.last_search_query, state.result_limit)
            refilter_needed = True

        # 6. Apply filters if needed
        if refilter_needed:
            update_filtered_results(state)
        
        # 7. Update scrolling position
        height, _ = stdscr.getmaxyx()
        visible_height = height - 2
        if state.selected_index != -1 and visible_height > 0:
            if state.selected_index < state.scroll_pos:
                state.scroll_pos = state.selected_index
            if state.selected_index >= state.scroll_pos + visible_height:
                state.scroll_pos = state.selected_index - visible_height + 1

if __name__ == "__main__":
    try:
        curses.wrapper(main_loop)
    except Exception as e:
        print(f"Failed to run application: {e}")

