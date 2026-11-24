"""
PeopleSoft MCP Server

This server provides tools to interact with PeopleSoft metadata and PeopleCode
"""

import sys
if sys.platform != "win32":
    raise RuntimeError("This MCP server requires Windows (uses winreg, ctypes.windll, and psaiapi.dll).")

import atexit
import ctypes
import os
import re
import signal
import threading
import tkinter as tk
from tkinter import messagebox
import winreg
from typing import Any, Dict, List, Optional, Tuple
from mcp.server.fastmcp import FastMCP

# --- Constants and Configuration ---

REGISTRY_PATH = r"Software\PeopleSoft\PeopleTools\Release8.40\Startup"
REQUIRED_ENV_VARS = ["PS_HOME", "ORACLE_HOME", "TNS_ADMIN"]
REQUIRED_REGISTRY_KEYS = ["DBName", "DBType", "UserId", "ConnectIdStd", "ClientConnectPswdV2Std"]

# Mode matrix:
# - MODE unset or "read": READ tools only
# - MODE="compile": READ + COMPILE tools
# - MODE="write": READ + COMPILE_AND_SAVE tools
VALID_MODES = {"read", "compile", "write"}
_MODE: str = os.getenv("MODE", "read").strip().lower()
if _MODE not in VALID_MODES:
    raise ValueError(f"Invalid MODE '{_MODE}'. Expected one of: {', '.join(sorted(VALID_MODES))}")
_COMPILE_MODE: bool = _MODE == "compile"
_WRITE_MODE: bool = _MODE == "write"
_SAVE_READ_OUTPUT: bool = _MODE in {"compile", "write"}

# --- Global State ---

mcp = FastMCP("PSIDEAPI")
_PSAIAPI: Optional[ctypes.CDLL] = None
_SIGNED_OFF: bool = False
_INIT_LOCK: threading.Lock = threading.Lock()
_WARMUP_DELAY_SECONDS = 3
_PASSWORD_TIMEOUT_SECONDS = 60
_AUTH_PENDING = "pending"
_AUTH_READY = "ready"
_AUTH_FAILED = "failed"
_AUTH_STATE = _AUTH_PENDING
_WARMUP_ERROR: Optional[str] = None
_AUTH_STATE_LOCK: threading.Lock = threading.Lock()
_STAGED_PEOPLECODE: List[tuple] = []
_STAGE_COUNTER: int = 0
_API_LOCK: threading.Lock = threading.Lock()  # Serializes all DLL access and staging operations


def _set_auth_state(state: str, error: Optional[str] = None) -> None:
    """Update the PeopleSoft authentication state."""
    global _AUTH_STATE, _WARMUP_ERROR
    with _AUTH_STATE_LOCK:
        _AUTH_STATE = state
        _WARMUP_ERROR = error


def _get_auth_status() -> Tuple[str, Optional[str]]:
    """Return the current PeopleSoft authentication state and failure reason."""
    with _AUTH_STATE_LOCK:
        return _AUTH_STATE, _WARMUP_ERROR

# --- Password Dialog Functions ---

def _dialog_ok(root: tk.Tk, pwd_entry: tk.Entry, result: Dict[str, Any]) -> None:
    """Handle OK button click."""
    pwd = pwd_entry.get()
    if not pwd:
        messagebox.showwarning("Password Required", "Please enter a password.", parent=root)
        pwd_entry.focus()
        return
    result['password'] = pwd
    root.destroy()


def _dialog_cancel(root: tk.Tk, result: Dict[str, Any]) -> None:
    """Handle Cancel button click."""
    result['cancelled'] = True
    root.destroy()


def _dialog_timeout(root: tk.Tk, result: Dict[str, Any]) -> None:
    """Auto-cancel the password dialog after the timeout expires."""
    result['cancelled'] = True
    result['timed_out'] = True
    try:
        root.destroy()
    except tk.TclError:
        pass


def prompt_for_user_password(db_name: str, user_id: str, db_type: str = "Oracle") -> Optional[str]:
    """
    Display a custom password dialog to get user password.
    """
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

    state = {'password': None, 'cancelled': False, 'timed_out': False}

    root = tk.Tk()
    root.withdraw()

    try:
        dpi = root.winfo_fpixels('1i')
        scale_factor = dpi / 96.0
    except Exception:
        scale_factor = 1.0

    root.overrideredirect(True)
    root.configure(bg='#aaaaaa')

    title_font = ('Segoe UI', 9, 'normal')
    label_font = ('Segoe UI', 9, 'normal')
    entry_font = ('Segoe UI', 9, 'normal')
    button_font = ('Segoe UI', 9, 'normal')
    close_font = ('Segoe UI', 10, 'normal')

    inner_frame = tk.Frame(root, bg='#f0f0f0')
    inner_frame.pack(fill='both', expand=True, padx=1, pady=1)

    title_bar_height = int(32 * scale_factor)
    title_bar = tk.Frame(inner_frame, bg='white', relief='flat', height=title_bar_height)
    title_bar.pack(fill='x', side='top')
    title_bar.pack_propagate(False)

    title_lbl = tk.Label(title_bar, text=f"PeopleSoft MCP Server Signon - {_PASSWORD_TIMEOUT_SECONDS}s timeout", bg='white', fg='black', font=title_font)
    title_lbl.pack(side='left', padx=10)

    close_btn = tk.Label(title_bar, text="\u2715", bg='white', fg='black', font=close_font, width=5)
    close_btn.pack(side='right', fill='y')

    close_btn.bind('<Enter>', lambda e: close_btn.config(bg='#e81123', fg='white'))
    close_btn.bind('<Leave>', lambda e: close_btn.config(bg='white', fg='black'))
    close_btn.bind('<Button-1>', lambda e: _dialog_cancel(root, state))

    tk.Frame(inner_frame, height=1, bg='#e5e5e5').pack(fill='x')

    main_frame = tk.Frame(inner_frame, padx=35, pady=25, bg='#f0f0f0')
    main_frame.pack(expand=True, fill='both')

    main_frame.grid_columnconfigure(0, weight=0)
    main_frame.grid_columnconfigure(1, weight=1)

    for row_idx, (label_text, value_text) in enumerate([
        ("Connection Type:", db_type),
        ("Database Name:", db_name),
        ("User ID:", user_id)
    ]):
        lbl = tk.Label(main_frame, text=label_text, bg='#f0f0f0', font=label_font, anchor='e')
        lbl.grid(row=row_idx, column=0, sticky='e', pady=6, padx=(0, 12))

        entry = tk.Entry(main_frame, width=28, font=entry_font)
        entry.insert(0, value_text)
        entry.configure(state='readonly')
        entry.grid(row=row_idx, column=1, sticky='ew', pady=6, padx=(0, 5))

    pwd_lbl = tk.Label(main_frame, text="Password:", bg='#f0f0f0', font=label_font, anchor='e')
    pwd_lbl.grid(row=3, column=0, sticky='e', pady=6, padx=(0, 12))

    pwd_entry = tk.Entry(main_frame, width=28, show='*', font=entry_font)
    pwd_entry.grid(row=3, column=1, sticky='ew', pady=6, padx=(0, 5))
    pwd_entry.focus()

    pwd_entry.bind('<Return>', lambda e: _dialog_ok(root, pwd_entry, state))

    timeout_msg = (
        f"This sign-on prompt expires after {_PASSWORD_TIMEOUT_SECONDS} seconds. "
        "If it expires, restart the MCP server."
    )
    timeout_lbl = tk.Label(
        main_frame,
        text=timeout_msg,
        bg='#f0f0f0',
        fg='#555555',
        font=label_font,
        justify='left',
        wraplength=int(330 * scale_factor),
        anchor='w',
    )
    timeout_lbl.grid(row=4, column=0, columnspan=2, sticky='ew', pady=(10, 0), padx=(0, 5))

    btn_frame = tk.Frame(main_frame, bg='#f0f0f0')
    btn_frame.grid(row=5, column=0, columnspan=2, pady=(25, 5))

    ok_btn = tk.Button(btn_frame, text="OK", width=10, relief='solid', bd=1, font=button_font, command=lambda: _dialog_ok(root, pwd_entry, state))
    ok_btn.pack(side='left', padx=8)

    cancel_btn = tk.Button(btn_frame, text="Cancel", width=10, relief='solid', bd=1, font=button_font, command=lambda: _dialog_cancel(root, state))
    cancel_btn.pack(side='left', padx=8)

    btn_frame.grid_configure(sticky='')

    root.update_idletasks()

    required_width = main_frame.winfo_reqwidth() + 62
    required_height = main_frame.winfo_reqheight() + title_bar_height + 42

    min_width = int(440 * scale_factor)
    min_height = int(280 * scale_factor)

    w = max(required_width, min_width)
    h = max(required_height, min_height)

    ws = root.winfo_screenwidth()
    hs = root.winfo_screenheight()
    x = (ws - w) // 2
    y = (hs - h) // 2

    root.geometry(f'{w}x{h}+{x}+{y}')
    root.minsize(min_width, min_height)

    root.deiconify()
    root.after(_PASSWORD_TIMEOUT_SECONDS * 1000, lambda: _dialog_timeout(root, state))
    root.mainloop()

    if state['timed_out']:
        raise TimeoutError(
            f"PeopleSoft password prompt timed out after {_PASSWORD_TIMEOUT_SECONDS} seconds."
        )

    if state['cancelled']:
        return None
    return state['password']


def get_user_password(db_name: str, user_id: str, db_type: str = "Oracle") -> str:
    """
    Get user password via UI prompt.
    
    Args:
        db_name: Database name
        user_id: User ID
        db_type: Connection type
        
    Returns:
        The user password
        
    Raises:
        RuntimeError: If password is not provided or cancelled
    """
    user_pwd = prompt_for_user_password(db_name, user_id, db_type)
    
    if not user_pwd:
        raise RuntimeError("User password is required. Authentication cancelled.")
    
    return user_pwd

# --- Environment and Configuration ---

def validate_environment_variables() -> Tuple[str, str, str]:
    """
    Validate and retrieve required environment variables.
    
    Returns:
        Tuple of (PS_HOME, ORACLE_HOME, TNS_ADMIN)
        
    Raises:
        EnvironmentError: If any required environment variable is missing.
    """
    ps_home = os.getenv("PS_HOME")
    oracle_home = os.getenv("ORACLE_HOME")
    tns_admin = os.getenv("TNS_ADMIN")
    
    if not all([ps_home, oracle_home, tns_admin]):
        raise EnvironmentError(
            f"Missing required environment variables: {', '.join(REQUIRED_ENV_VARS)}"
        )
    
    return ps_home, oracle_home, tns_admin


def setup_system_path(ps_home: str, oracle_home: str) -> None:
    """
    Configure the system PATH for PeopleSoft and Oracle components.
    
    Args:
        ps_home: PeopleSoft home directory
        oracle_home: Oracle home directory
    """
    os.environ["PATH"] = (
        f"{oracle_home}\\BIN;"
        f"{ps_home}\\bin\\client\\winx86;"
        f"{oracle_home}\\bin;"
        f"{os.environ.get('PATH', '')}"
    )


def read_registry_values() -> Dict[str, str]:
    """
    Read PeopleSoft configuration values from Windows Registry.
    
    Returns:
        Dictionary of registry key-value pairs.
        
    Raises:
        OSError: If registry access fails.
    """
    values = {}
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REGISTRY_PATH) as key:
            i = 0
            while True:
                try:
                    name, value, _ = winreg.EnumValue(key, i)
                    values[name] = value
                    i += 1
                except OSError:
                    break
    except OSError as e:
        raise OSError(f"Failed to read PeopleSoft registry at {REGISTRY_PATH}: {e}")
    
    return values


def validate_registry_values(registry_values: Dict[str, str]) -> None:
    """
    Validate that all required registry values are present.
    
    Args:
        registry_values: Dictionary of registry values
        
    Raises:
        EnvironmentError: If any required registry value is missing.
    """
    missing = [key for key in REQUIRED_REGISTRY_KEYS if not registry_values.get(key)]
    if missing:
        raise EnvironmentError(
            f"Missing required PeopleSoft registry values: {', '.join(missing)}"
        )


def parse_tnsnames(tns_admin: str, db_name: str) -> Tuple[str, str, str]:
    """
    Parse TNS configuration to extract database connection details.
    
    Uses parenthesis-balanced parsing to correctly handle multi-line
    and nested TNS entries. Matches db_name with or without a domain
    suffix (e.g., 'MYDB' matches 'MYDB.domain.com=...').
    
    Args:
        tns_admin: Path to TNS configuration directory
        db_name: Database name to look up
        
    Returns:
        Tuple of (host, port, service_name)
        
    Raises:
        FileNotFoundError: If tnsnames.ora file is not found.
        ValueError: If the database entry is not found in tnsnames.ora.
    """
    tns_path = os.path.join(tns_admin, "tnsnames.ora")
    
    if not os.path.exists(tns_path):
        raise FileNotFoundError(f"tnsnames.ora not found at {tns_path}")
    
    with open(tns_path, "r") as f:
        content = f.read()
    
    # Strip comment lines
    lines = [line for line in content.splitlines() if not line.strip().startswith("#")]
    content = "\n".join(lines)
    
    # Find the entry: db_name optionally followed by a domain suffix, then '='
    # e.g., "T63AR018=" or "T63AR018.oradev.oraclecorp.com="
    entry_pattern = re.compile(
        rf"(?im)^\s*{re.escape(db_name)}(?:\.[^\s=]+)?\s*=",
    )
    entry_match = entry_pattern.search(content)
    
    if not entry_match:
        raise ValueError(f"TNS entry not found for DB name: {db_name}")
    
    # Find the first '(' after the '=' to start balanced parsing
    eq_pos = entry_match.end()
    paren_start = content.find("(", eq_pos)
    
    if paren_start == -1:
        raise ValueError(f"Invalid TNS entry format for {db_name}: no opening parenthesis found")
    
    # Walk forward counting parentheses to find the complete block
    depth = 0
    pos = paren_start
    while pos < len(content):
        ch = content[pos]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                break
        pos += 1
    
    if depth != 0:
        raise ValueError(f"Invalid TNS entry format for {db_name}: unbalanced parentheses")
    
    block = content[paren_start:pos + 1]
    
    # Extract HOST, PORT, SERVICE_NAME from the balanced block
    host_match = re.search(r"HOST\s*=\s*([^)]+)", block, re.IGNORECASE)
    service_match = re.search(r"SERVICE_NAME\s*=\s*([^)]+)", block, re.IGNORECASE)
    port_match = re.search(r"PORT\s*=\s*(\d+)", block, re.IGNORECASE)
    
    if not host_match or not service_match:
        raise ValueError(f"Invalid TNS entry format for {db_name}")
    
    host = host_match.group(1).strip()
    service = service_match.group(1).strip()
    port = port_match.group(1) if port_match else "1521"
    
    return host, port, service

# --- PSAIAPI DLL Management ---

def load_psaiapi_dll(ps_home: str) -> ctypes.CDLL:
    """
    Load the PeopleSoft AI API DLL.
    
    Args:
        ps_home: PeopleSoft home directory
        
    Returns:
        Loaded CDLL object
        
    Raises:
        RuntimeError: If DLL loading fails.
    """
    dll_path = os.path.join(ps_home, "bin", "client", "winx86", "psaiapi.dll")
    
    try:
        return ctypes.CDLL(dll_path)
    except OSError as e:
        raise RuntimeError(f"Failed to load psaiapi.dll from {dll_path}: {e}")


def configure_dll_functions(psaiapi: ctypes.CDLL) -> None:
    """
    Configure function signatures for PSAIAPI DLL functions.
    
    Args:
        psaiapi: Loaded PSAIAPI DLL object
    """
    psaiapi.SignOnDatabase.argtypes = [ctypes.c_wchar_p] * 9
    psaiapi.SignOnDatabase.restype = ctypes.c_wchar_p

    psaiapi.GetFieldData.argtypes = [ctypes.c_wchar_p]
    psaiapi.GetFieldData.restype = ctypes.POINTER(ctypes.c_wchar)

    psaiapi.FreeFieldDetails.argtypes = [ctypes.POINTER(ctypes.c_wchar)]
    psaiapi.FreeFieldDetails.restype = None

    psaiapi.GetRecordData.argtypes = [ctypes.c_wchar_p]
    psaiapi.GetRecordData.restype = ctypes.POINTER(ctypes.c_wchar)

    psaiapi.FreeRecordDetails.argtypes = [ctypes.POINTER(ctypes.c_wchar)]
    psaiapi.FreeRecordDetails.restype = None

    psaiapi.GetPageData.argtypes = [ctypes.c_wchar_p]
    psaiapi.GetPageData.restype = ctypes.POINTER(ctypes.c_wchar)

    psaiapi.FreePageDetails.argtypes = [ctypes.POINTER(ctypes.c_wchar)]
    psaiapi.FreePageDetails.restype = None

    psaiapi.GetComponentData.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
    psaiapi.GetComponentData.restype = ctypes.POINTER(ctypes.c_wchar)

    psaiapi.FreeComponentDetails.argtypes = [ctypes.POINTER(ctypes.c_wchar)]
    psaiapi.FreeComponentDetails.restype = None

    psaiapi.GetAppPackageData.argtypes = (ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_wchar_p)
    psaiapi.GetAppPackageData.restype = ctypes.POINTER(ctypes.c_wchar)

    psaiapi.FreeAppPackageDetails.argtypes = [ctypes.POINTER(ctypes.c_wchar)]
    psaiapi.FreeAppPackageDetails.restype = None

    psaiapi.GetAppEngineData.argtypes = [ctypes.c_wchar_p]
    psaiapi.GetAppEngineData.restype = ctypes.POINTER(ctypes.c_wchar)

    psaiapi.FreeAppEngineDetails.argtypes = [ctypes.POINTER(ctypes.c_wchar)]
    psaiapi.FreeAppEngineDetails.restype = None

    psaiapi.GetProjectData.argtypes = [ctypes.c_wchar_p]
    psaiapi.GetProjectData.restype = ctypes.POINTER(ctypes.c_wchar_p)

    psaiapi.GetPeopleCodeData.argtypes = (ctypes.c_int, ctypes.c_wchar_p, ctypes.c_int, ctypes.c_wchar_p, ctypes.c_int, ctypes.c_wchar_p, ctypes.c_int, ctypes.c_wchar_p, ctypes.c_int, ctypes.c_wchar_p, ctypes.c_int, ctypes.c_wchar_p, ctypes.c_int, ctypes.c_wchar_p)
    psaiapi.GetPeopleCodeData.restype = ctypes.POINTER(ctypes.c_wchar)

    psaiapi.FreePCodeDetails.argtypes = [ctypes.POINTER(ctypes.c_wchar)]
    psaiapi.FreePCodeDetails.restype = None

    psaiapi.SignOffDatabase.argtypes = ()
    psaiapi.SignOffDatabase.restype = None

    if _COMPILE_MODE:
        psaiapi.CompilePeopleCodeObject.argtypes = (ctypes.c_int, ctypes.c_wchar_p, ctypes.c_int, ctypes.c_wchar_p, ctypes.c_int, ctypes.c_wchar_p, ctypes.c_int, ctypes.c_wchar_p, ctypes.c_int, ctypes.c_wchar_p, ctypes.c_int, ctypes.c_wchar_p, ctypes.c_int, ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_int)
        psaiapi.CompilePeopleCodeObject.restype = ctypes.POINTER(ctypes.c_wchar)

    if _WRITE_MODE:
        psaiapi.UpdateMultiPeopleCodeData.argtypes = (ctypes.POINTER(ctypes.c_wchar_p), ctypes.c_int, ctypes.POINTER(ctypes.c_wchar_p), ctypes.c_int)
        psaiapi.UpdateMultiPeopleCodeData.restype  = ctypes.POINTER(ctypes.c_wchar)


def authenticate_database(psaiapi: ctypes.CDLL, ps_home: str, db_name: str, db_type: str, db_host: str, user_id: str, user_pwd: str, acc_id: str, acc_pwd: str, owner_id: str) -> None:
    """
    Authenticate and sign on to the PeopleSoft database.
    
    Args:
        psaiapi: Loaded PSAIAPI DLL object
        ps_home: PeopleSoft home directory
        db_name: Database name
        db_type: Database type
        db_host: Database host
        user_id: User ID
        user_pwd: User password
        acc_id: Access ID
        acc_pwd: Access password
        owner_id: Owner ID
        
    Raises:
        RuntimeError: If database sign-on fails.
    """
    result = psaiapi.SignOnDatabase(ps_home, db_name, db_type, db_host, user_id, user_pwd, acc_id, acc_pwd, owner_id)
    
    if not result:
        raise RuntimeError("Database sign-on failed: No response from database")
    
    if result != "Successfully connected to DB":
        raise RuntimeError(f"Database sign-on failed: {result}")


def get_psaiapi() -> ctypes.CDLL:
    """
    Get the initialized PSAIAPI instance.
    
    Returns:
        The initialized PSAIAPI DLL object.
        
    Raises:
        RuntimeError: If PSAIAPI has not been initialized.
    """
    if _PSAIAPI is None:
        auth_state, auth_error = _get_auth_status()
        if auth_state == _AUTH_FAILED:
            raise RuntimeError(f"PeopleSoft authentication failed: {auth_error}")
        if auth_state == _AUTH_PENDING:
            raise RuntimeError(
                "PeopleSoft sign-on is still pending. Complete the password prompt, then retry."
            )
        raise RuntimeError("PSAIAPI not initialized even though authentication is marked ready.")
    return _PSAIAPI

# --- MCP Tools: Metadata ---

@mcp.tool()
def get_field_details(field: str) -> str:
    """Retrieve PeopleSoft Field definition metadata.

    Args:
        field: Name of the Field in PeopleSoft.

    Returns:
        Structured text output describing the field definition and 
        other metadata fetched from the database.
    """
    if not field:
        raise ValueError("field cannot be empty.")

    api = get_psaiapi()
    with _API_LOCK:
        ptr = api.GetFieldData(field)
        try:
            if not ptr:
                return ""
            return ctypes.wstring_at(ptr)
        finally:
            if ptr:
                api.FreeFieldDetails(ptr)


@mcp.tool()
def get_record_details(record: str) -> str:
    """Retrieve PeopleSoft record definition metadata.

    Args:
        record: Name of the record (table or view) in PeopleSoft.

    Returns:
        Structured text output describing the record definition, fields, keys,
        and other metadata fetched from the database.
    """
    if not record:
        raise ValueError("record cannot be empty.")

    api = get_psaiapi()
    with _API_LOCK:
        ptr = api.GetRecordData(record)
        try:
            if not ptr:
                return ""
            return ctypes.wstring_at(ptr)
        finally:
            if ptr:
                api.FreeRecordDetails(ptr)


@mcp.tool()
def get_page_details(page: str) -> str:
    """Retrieve PeopleSoft page definition metadata.

    Args:
        page: Name of the page definition.

    Returns:
        Structured text output describing the layout, records, fields
        associated with the specified page and other metadata.
    """
    if not page:
        raise ValueError("page cannot be empty.")

    api = get_psaiapi()
    with _API_LOCK:
        ptr = api.GetPageData(page)
        try:
            if not ptr:
                return ""
            return ctypes.wstring_at(ptr)
        finally:
            if ptr:
                api.FreePageDetails(ptr)


@mcp.tool()
def get_component_details(component: str, market: str) -> str:
    """Retrieve PeopleSoft component definition metadata.

    Args:
        component: Name of the component
        market: Market code associated with the component.

    Returns:
        Structured text representing the component definition, including
        pages, record and other metadata.
    """
    missing = [name for name, val in [("component", component), ("market", market)] if not val]
    if missing:
        raise ValueError(f"{", ".join(missing)} cannot be empty.")

    api = get_psaiapi()
    with _API_LOCK:
        ptr = api.GetComponentData(component, market)
        try:
            if not ptr:
                return ""
            return ctypes.wstring_at(ptr)
        finally:
            if ptr:
                api.FreeComponentDetails(ptr)


@mcp.tool()
def get_application_package_details(application_package: str) -> str:
    """Retrieve PeopleSoft Application Package definition metadata.

    Args:
        application_package: Name of the Application Package in PeopleSoft.

    Returns:
        Structured text output describing the Application Package structure with
        application classes and sub packages.
    """
    if not application_package:
        raise ValueError("application_package cannot be empty.")

    api = get_psaiapi()
    with _API_LOCK:
        ptr = api.GetAppPackageData(application_package, application_package, ".")
        try:
            if not ptr:
                return ""
            return ctypes.wstring_at(ptr)
        finally:
            if ptr:
                api.FreeAppPackageDetails(ptr)


@mcp.tool()
def get_app_engine_details(app_engine: str) -> str:
    """Retrieve PeopleSoft Application Engine definition metadata.

    Args:
        app_engine: Name of the Application Engine program.

    Returns:
        Structured text output describing the Application Engine definition and 
        other metadata fetched from the database.
    """
    if not app_engine:
        raise ValueError("app_engine cannot be empty.")

    api = get_psaiapi()
    with _API_LOCK:
        ptr = api.GetAppEngineData(app_engine)
        try:
            if not ptr:
                return ""
            return ctypes.wstring_at(ptr)
        finally:
            if ptr:
                api.FreeAppEngineDetails(ptr)


@mcp.tool()
def get_project_details(project_name: str) -> str:
    """Retrieve PeopleSoft project definition metadata.

    Args:
        project_name: Name of the project in PeopleSoft.

    Returns:
        Project items joined as a newline-delimited string.
    """
    if not project_name:
        raise ValueError("project_name cannot be empty.")
    
    api = get_psaiapi()
    with _API_LOCK:
        ptr = api.GetProjectData(project_name)
        if not ptr:
            return ""
        project_items = []
        i = 0 
        while True:
            val = ptr[i]
            if not val:
                break
            project_items.append(val)
            i += 1
    
    return "\n".join(project_items)

# --- Helper Functions ---

def _save_peoplecode_to_file(parts: List[Tuple[str, str]], content: str, output_dir: Optional[str] = None) -> Optional[str]:
    """
    Save PeopleCode content to a file when write mode is enabled.
    
    Args:
        parts: List of (label, value) tuples for building the filename.
               Empty values are skipped. Example: [("RECORD", "JOB"), ("FIELD", "EMPLID")].
        content: PeopleCode content to write.
        output_dir: Directory path for the output file. Falls back to
                   WORKSPACE_DIR env var, then current working directory.
    
    Returns:
        A message indicating the save path, or None if write mode is off.
    """
    if not _SAVE_READ_OUTPUT or content.strip() == "PeopleCode data not found.":
        return None

    save_dir = output_dir if output_dir else os.getenv("WORKSPACE_DIR", os.getcwd())
    save_dir = os.path.realpath(save_dir)
    
    if not os.path.isdir(save_dir):
        return f"Output directory does not exist: {save_dir}"
    
    filename = "_".join(f"{label}_{value}" for label, value in parts if value) + ".txt"
    safe_filename = "".join(c for c in filename if c.isalnum() or c in ('.', '_', '-')).strip()
    full_path = os.path.join(save_dir, safe_filename)
    
    try:
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(content)
    except OSError as e:
        return f"Failed to save PeopleCode to {full_path}: {e}"
    
    return f"Copy of the PeopleCode is saved in write enabled mode at {full_path}"

# --- MCP Tools: PeopleCode ---

@mcp.tool()
def get_record_peoplecode(record: str, field: str, event: str, output_dir: str) -> str:
    """Fetch PeopleCode associated with a specific record field event.

    Args:
        record: Name of the record (e.g., 'JOB').
        field: Field name within the record (e.g., 'EMPLID').
        event: Event name (e.g., 'FieldChange', 'SaveEdit').
        output_dir: Directory path where the file should be saved if WRITE_MODE is enabled. Pass the current working directory.

    Returns:
        Either the PeopleCode source directly, or a message starting with "Refer to PeopleCode in..."
        indicating a file path. If you receive a file reference message, you MUST read that file
        to access the actual PeopleCode content.
    """
    missing = [name for name, val in [("record", record), ("field", field), ("event", event)] if not val]
    if missing:
        raise ValueError(f"{", ".join(missing)} cannot be empty.")

    api = get_psaiapi()
    with _API_LOCK:
        ptr = api.GetPeopleCodeData(1, record, 2, field, 12, event, 0, "", 0, "", 0, "", 0, "")
        try:
            if not ptr:
                return ""
            result_str = ctypes.wstring_at(ptr)
        finally:
            if ptr:
                api.FreePCodeDetails(ptr)
    file_msg = _save_peoplecode_to_file([("RECORD", record), ("FIELD", field), ("METHOD", event)], result_str, output_dir)
    return file_msg if file_msg else result_str


@mcp.tool()
def get_page_peoplecode(page: str, output_dir: str) -> str:
    """Fetch PeopleCode defined at the page level.

    Args:
        page: Page name (e.g., 'JOB_DATA1').
        output_dir: Directory path where the file should be saved if WRITE_MODE is enabled. Pass the current working directory.

    Returns:
        Either the PeopleCode source directly, or a message starting with "Refer to PeopleCode in..."
        indicating a file path. If you receive a file reference message, you MUST read that file
        to access the actual PeopleCode content.
    """
    if not page:
        raise ValueError("page cannot be empty.")

    api = get_psaiapi()
    with _API_LOCK:
        ptr = api.GetPeopleCodeData(9, page, 12, "Activate", 0, "", 0, "", 0, "", 0, "", 0, "")
        try:
            if not ptr:
                return ""
            result_str = ctypes.wstring_at(ptr)
        finally:
            if ptr:
                api.FreePCodeDetails(ptr)
    file_msg = _save_peoplecode_to_file([("PAGE", page), ("METHOD", "Activate")], result_str, output_dir)
    return file_msg if file_msg else result_str


@mcp.tool()
def get_page_field_peoplecode(page: str, page_field: str, event: str, output_dir: str) -> str:
    """Fetch PeopleCode for a specific page field event.

    Args:
        page: Page name.
        page_field: Field name on the page.
        event: Event name.
        output_dir: Directory path where the file should be saved if WRITE_MODE is enabled. Pass the current working directory.

    Returns:
        Either the PeopleCode source directly, or a message starting with "Refer to PeopleCode in..."
        indicating a file path. If you receive a file reference message, you MUST read that file
        to access the actual PeopleCode content.
    """
    missing = [name for name, val in [("page", page), ("page_field", page_field), ("event", event)] if not val]
    if missing:
        raise ValueError(f"{", ".join(missing)} cannot be empty.")

    api = get_psaiapi()
    with _API_LOCK:
        ptr = api.GetPeopleCodeData(9, page, 67, page_field, 12, event, 0, "", 0, "", 0, "", 0, "")
        try:
            if not ptr:
                return ""
            result_str = ctypes.wstring_at(ptr)
        finally:
            if ptr:
                api.FreePCodeDetails(ptr)
    file_msg = _save_peoplecode_to_file([("PAGE", page), ("PAGEFIELD", page_field), ("METHOD", event)], result_str, output_dir)
    return file_msg if file_msg else result_str


@mcp.tool()
def get_component_peoplecode(component: str, market: str, event: str, output_dir: str) -> str:
    """Fetch PeopleCode attached to a component event.

    Args:
        component: Component name.
        market: Market code.
        event: Component-level event name.
        output_dir: Directory path where the file should be saved if WRITE_MODE is enabled. Pass the current working directory.

    Returns:
        Either the PeopleCode source directly, or a message starting with "Refer to PeopleCode in..."
        indicating a file path. If you receive a file reference message, you MUST read that file
        to access the actual PeopleCode content.
    """
    missing = [name for name, val in [("component", component), ("market", market), ("event", event)] if not val]
    if missing:
        raise ValueError(f"{", ".join(missing)} cannot be empty.")

    api = get_psaiapi()
    with _API_LOCK:
        ptr = api.GetPeopleCodeData(10, component, 39, market, 12, event, 0, "", 0, "", 0, "", 0, "")
        try:
            if not ptr:
                return ""
            result_str = ctypes.wstring_at(ptr)
        finally:
            if ptr:
                api.FreePCodeDetails(ptr)
    file_msg = _save_peoplecode_to_file([("COMPONENT", component), ("MARKET", market), ("METHOD", event)], result_str, output_dir)
    return file_msg if file_msg else result_str


@mcp.tool()
def get_component_record_peoplecode(component: str, market: str, record: str, event: str, output_dir: str) -> str:
    """Fetch PeopleCode for a record used within a specific component.

    Args:
        component: Component name.
        market: Market code.
        record: Record name used within the component.
        event: Event name.
        output_dir: Directory path where the file should be saved if WRITE_MODE is enabled. Pass the current working directory.

    Returns:
        Either the PeopleCode source directly, or a message starting with "Refer to PeopleCode in..."
        indicating a file path. If you receive a file reference message, you MUST read that file
        to access the actual PeopleCode content.
    """
    missing = [name for name, val in [("component", component), ("market", market), ("record", record), ("event", event)] if not val]
    if missing:
        raise ValueError(f"{", ".join(missing)} cannot be empty.")

    api = get_psaiapi()
    with _API_LOCK:
        ptr = api.GetPeopleCodeData(10, component, 39, market, 1, record, 12, event, 0, "", 0, "", 0, "")
        try:
            if not ptr:
                return ""
            result_str = ctypes.wstring_at(ptr)
        finally:
            if ptr:
                api.FreePCodeDetails(ptr)
    file_msg = _save_peoplecode_to_file([("COMPONENT", component), ("MARKET", market), ("RECORD", record), ("METHOD", event)], result_str, output_dir)
    return file_msg if file_msg else result_str


@mcp.tool()
def get_component_record_field_peoplecode(component: str, market: str, record: str, field: str, event: str, output_dir: str) -> str:
    """Fetch PeopleCode for a field on a record used within a component.

    Args:
        component: Component name.
        market: Market code.
        record: Record name within the component.
        field: Field name within the record.
        event: Field-level event.
        output_dir: Directory path where the file should be saved if WRITE_MODE is enabled. Pass the current working directory.

    Returns:
        Either the PeopleCode source directly, or a message starting with "Refer to PeopleCode in..."
        indicating a file path. If you receive a file reference message, you MUST read that file
        to access the actual PeopleCode content.
    """
    missing = [name for name, val in [("component", component), ("market", market), ("record", record), ("field", field), ("event", event)] if not val]
    if missing:
        raise ValueError(f"{", ".join(missing)} cannot be empty.")

    api = get_psaiapi()
    with _API_LOCK:
        ptr = api.GetPeopleCodeData(10, component, 39, market, 1, record, 2, field, 12, event, 0, "", 0, "")
        try:
            if not ptr:
                return ""
            result_str = ctypes.wstring_at(ptr)
        finally:
            if ptr:
                api.FreePCodeDetails(ptr)
    file_msg = _save_peoplecode_to_file([("COMPONENT", component), ("MARKET", market), ("RECORD", record), ("FIELD", field), ("METHOD", event)], result_str, output_dir)
    return file_msg if file_msg else result_str


@mcp.tool()
def get_application_class_peoplecode(application_class: str, application_package: str, output_dir: str, subpackage1: str = "", subpackage2: str = "") -> str:
    """Fetch PeopleCode for an Application Class defined within an Application Package.

    Args:
        application_class: Class name containing PeopleCode (e.g., 'MY_CLASS').
        application_package: Top-level application package (e.g., 'MY_APP_PKG').
        output_dir: Directory path where the file should be saved if WRITE_MODE is enabled. Pass the current working directory.
        subpackage1: (Optional) First-level subpackage within the application package.
        subpackage2: (Optional) Second-level subpackage.

    Notes:
        - The structure corresponds to: package:subpackage1:subpackage2:class:OnExecute
        - Missing hierarchy levels are skipped but positional padding is preserved.

    Returns:
        Either the PeopleCode source directly, or a message starting with "Refer to PeopleCode in..."
        indicating a file path. If you receive a file reference message, you MUST read that file
        to access the actual PeopleCode content.
    """
    missing = [name for name, val in [("application_class", application_class), ("application_package", application_package)] if not val]
    if missing:
        raise ValueError(f"{", ".join(missing)} cannot be empty.")

    params = []
    
    if application_package:
        params += [104, application_package]
    if subpackage1:
        params += [105, subpackage1]
    if subpackage2:
        params += [106, subpackage2]
    if application_class:
        params += [107, application_class]
    
    params += [12, "OnExecute"]
    
    while len(params) < 14:
        params += [0, ""]
    
    api = get_psaiapi()
    with _API_LOCK:
        ptr = api.GetPeopleCodeData(*params)
        try:
            if not ptr:
                return ""
            result_str = ctypes.wstring_at(ptr)
        finally:
            if ptr:
                api.FreePCodeDetails(ptr)
    file_msg = _save_peoplecode_to_file(
        [("APPLICATION_PACKAGE", application_package), ("APPLICATION_PACKAGE1", subpackage1),
         ("APPLICATION_PACKAGE2", subpackage2), ("APPLICATION_CLASS", application_class),
         ("METHOD", "OnExecute")],
        result_str, output_dir)
    return file_msg if file_msg else result_str


@mcp.tool()
def get_app_engine_peoplecode(ae_program: str, section: str, market: str, step: str, output_dir: str, dbtype: str = "default", effdt: str = "1900-01-01") -> str:
    """Fetch PeopleCode from a PeopleSoft Application Engine (AE) program.

    Args:
        ae_program: The Application Engine program name.
        section: The section name within the AE program.
        market: The market code associated with the AE program.
        step: The step name within the section.
        output_dir: Directory path where the file should be saved if WRITE_MODE is enabled. Pass the current working directory.
        dbtype: The database type or AE DB context.
        effdt: The effective date for the AE.

    Returns:
        Either the PeopleCode source directly, or a message starting with "Refer to PeopleCode in..."
        indicating a file path. If you receive a file reference message, you MUST read that file
        to access the actual PeopleCode content.
    """
    missing = [name for name, val in [("ae_program", ae_program), ("section", section), ("market", market), ("step", step)] if not val]
    if missing:
        raise ValueError(f"{", ".join(missing)} cannot be empty.")

    api = get_psaiapi()
    with _API_LOCK:
        ptr = api.GetPeopleCodeData(66, ae_program, 77, section, 39, market, 20, dbtype, 21, effdt, 78, step, 12, "OnExecute")
        try:
            if not ptr:
                return ""
            result_str = ctypes.wstring_at(ptr)
        finally:
            if ptr:
                api.FreePCodeDetails(ptr)
    file_msg = _save_peoplecode_to_file(
        [("AEAPPLICATIONID", ae_program), ("AESECTION", section), ("MARKET", market),
         ("DBTYPE", dbtype), ("EFFDT", effdt), ("AESTEP", step), ("METHOD", "OnExecute")],
        result_str, output_dir)
    return file_msg if file_msg else result_str

# --- PeopleCode File Helpers ---

def _read_peoplecode_file(peoplecode_file: str) -> str:
    """
    Read PeopleCode source text from a file.

    Args:
        peoplecode_file: Absolute path to a file containing PeopleCode source text.

    Returns:
        The PeopleCode source text.

    Raises:
        FileNotFoundError: If the file does not exist.
        ValueError: If the file is empty.
    """
    if not os.path.exists(peoplecode_file):
        raise FileNotFoundError(f"PeopleCode file not found: {peoplecode_file}")

    with open(peoplecode_file, "r", encoding="utf-8") as f:
        peoplecode = f.read()

    if not peoplecode.strip():
        raise ValueError(f"PeopleCode file is empty: {peoplecode_file}")

    return peoplecode


# --- MCP Tools: Compile PeopleCode ---

def _compile_peoplecode_object(int_str_pairs: list, peoplecode: str) -> str:
    """Call CompilePeopleCodeObject via the DLL."""
    padded = list(int_str_pairs)
    while len(padded) < 7:
        padded.append((0, ""))

    args = []
    for type_code, value in padded:
        args.append(type_code)
        args.append(value)
    args.append(peoplecode)
    args.append(1)

    api = get_psaiapi()
    with _API_LOCK:
        result_ptr = api.CompilePeopleCodeObject(*args)
        try:
            return ctypes.wstring_at(result_ptr) if result_ptr else "No response from CompilePeopleCodeObject API."
        finally:
            if result_ptr:
                api.FreePCodeDetails(result_ptr)


def compile_record_peoplecode(record: str, field: str, event: str, peoplecode_file: str) -> str:
    """Compile PeopleCode for a record field event WITHOUT saving to the database.

    This reads PeopleCode from the specified file path and compiles it in-memory.
    No database save is performed by this tool.

    Args:
        record: Name of the record (e.g., 'JOB').
        field: Field name within the record (e.g., 'EMPLID').
        event: Event name (e.g., 'FieldChange', 'SaveEdit').
        peoplecode_file: Absolute path to a file containing the PeopleCode source text.

    Returns:
        Compile output text returned by the PeopleSoft API.
    """
    peoplecode = _read_peoplecode_file(peoplecode_file)
    return _compile_peoplecode_object([(1, record), (2, field), (12, event)], peoplecode)


def compile_page_peoplecode(page: str, peoplecode_file: str) -> str:
    """Compile PeopleCode for a page Activate event WITHOUT saving to the database.

    This reads PeopleCode from the specified file path and compiles it in-memory.
    No database save is performed by this tool.

    Args:
        page: Page name (e.g., 'JOB_DATA1').
        peoplecode_file: Absolute path to a file containing the PeopleCode source text.

    Returns:
        Compile output text returned by the PeopleSoft API.
    """
    peoplecode = _read_peoplecode_file(peoplecode_file)
    return _compile_peoplecode_object([(9, page), (12, "Activate")], peoplecode)


def compile_page_field_peoplecode(page: str, page_field: str, event: str, peoplecode_file: str) -> str:
    """Compile PeopleCode for a page field event WITHOUT saving to the database.

    This reads PeopleCode from the specified file path and compiles it in-memory.
    No database save is performed by this tool.

    Args:
        page: Page name.
        page_field: Field name on the page.
        event: Event name.
        peoplecode_file: Absolute path to a file containing the PeopleCode source text.

    Returns:
        Compile output text returned by the PeopleSoft API.
    """
    peoplecode = _read_peoplecode_file(peoplecode_file)
    return _compile_peoplecode_object([(9, page), (67, page_field), (12, event)], peoplecode)


def compile_component_peoplecode(component: str, market: str, event: str, peoplecode_file: str) -> str:
    """Compile PeopleCode for a component event WITHOUT saving to the database.

    This reads PeopleCode from the specified file path and compiles it in-memory.
    No database save is performed by this tool.

    Args:
        component: Component name.
        market: Market code associated with the component.
        event: Component-level event name.
        peoplecode_file: Absolute path to a file containing the PeopleCode source text.

    Returns:
        Compile output text returned by the PeopleSoft API.
    """
    peoplecode = _read_peoplecode_file(peoplecode_file)
    return _compile_peoplecode_object([(10, component), (39, market), (12, event)], peoplecode)


def compile_component_record_peoplecode(component: str, market: str, record: str, event: str, peoplecode_file: str) -> str:
    """Compile PeopleCode for a record within a component WITHOUT saving to the database.

    This reads PeopleCode from the specified file path and compiles it in-memory.
    No database save is performed by this tool.

    Args:
        component: Component name.
        market: Market code.
        record: Record name used within the component.
        event: Event name.
        peoplecode_file: Absolute path to a file containing the PeopleCode source text.

    Returns:
        Compile output text returned by the PeopleSoft API.
    """
    peoplecode = _read_peoplecode_file(peoplecode_file)
    return _compile_peoplecode_object([(10, component), (39, market), (1, record), (12, event)], peoplecode)


def compile_component_record_field_peoplecode(component: str, market: str, record: str, field: str, event: str, peoplecode_file: str) -> str:
    """Compile PeopleCode for a field on a record within a component WITHOUT saving to the database.

    This reads PeopleCode from the specified file path and compiles it in-memory.
    No database save is performed by this tool.

    Args:
        component: Component name.
        market: Market code.
        record: Record name within the component.
        field: Field name within the record.
        event: Field-level event.
        peoplecode_file: Absolute path to a file containing the PeopleCode source text.

    Returns:
        Compile output text returned by the PeopleSoft API.
    """
    peoplecode = _read_peoplecode_file(peoplecode_file)
    return _compile_peoplecode_object([(10, component), (39, market), (1, record), (2, field), (12, event)], peoplecode)


def compile_application_class_peoplecode(application_class: str, application_package: str, peoplecode_file: str, subpackage1: str = "", subpackage2: str = "") -> str:
    """Compile PeopleCode for an Application Class WITHOUT saving to the database.

    This reads PeopleCode from the specified file path and compiles it in-memory.
    No database save is performed by this tool.

    Args:
        application_class: Class name containing PeopleCode (e.g., 'MY_CLASS').
        application_package: Top-level application package (e.g., 'MY_APP_PKG').
        peoplecode_file: Absolute path to a file containing the PeopleCode source text.
        subpackage1: (Optional) First-level subpackage within the application package.
        subpackage2: (Optional) Second-level subpackage within the application package.

    Returns:
        Compile output text returned by the PeopleSoft API.
    """
    peoplecode = _read_peoplecode_file(peoplecode_file)

    pairs = []
    if application_package:
        pairs.append((104, application_package))
    if subpackage1:
        pairs.append((105, subpackage1))
    if subpackage2:
        pairs.append((106, subpackage2))
    if application_class:
        pairs.append((107, application_class))
    pairs.append((12, "OnExecute"))

    return _compile_peoplecode_object(pairs, peoplecode)


def compile_app_engine_peoplecode(ae_program: str, section: str, market: str, step: str, peoplecode_file: str, dbtype: str = "default", effdt: str = "1900-01-01") -> str:
    """Compile PeopleCode for an Application Engine step WITHOUT saving to the database.

    This reads PeopleCode from the specified file path and compiles it in-memory.
    No database save is performed by this tool.

    Args:
        ae_program: The Application Engine program name.
        section: The section name within the AE program.
        market: The market code associated with the AE program.
        step: The step name within the section.
        peoplecode_file: Absolute path to a file containing the PeopleCode source text.
        dbtype: The database type or AE DB context.
        effdt: The effective date for the AE.

    Returns:
        Compile output text returned by the PeopleSoft API.
    """
    peoplecode = _read_peoplecode_file(peoplecode_file)
    return _compile_peoplecode_object([(66, ae_program), (77, section), (39, market), (20, dbtype), (21, effdt), (78, step), (12, "OnExecute")], peoplecode)


# --- MCP Tools: Save PeopleCode ---

def _build_identifier_strings(key_value_pairs: list) -> List[str]:
    """
    Build a flat list of 14 string elements (7 key-value pairs) for the C++ API.

    Takes a list of (int_type_code, string_value) tuples representing the
    meaningful identifier parts, pads remaining slots with ("0", ""), and
    returns all values as strings (the C++ API expects c_wchar_p for everything
    in the flat items array).

    Args:
        key_value_pairs: List of (type_code: int, value: str) tuples.
                         At most 7 pairs.

    Returns:
        A flat list of exactly 14 strings: [type1, val1, type2, val2, ...].
    """
    result = []
    for type_code, value in key_value_pairs:
        result.append(str(type_code))
        result.append(value)

    while len(result) < 14:
        result.append("0")
        result.append("")

    return result


def _stage_peoplecode(identifier_strings: List[str], peoplecode: str, label: str) -> str:
    """
    Add a PeopleCode entry to the staging list.

    Args:
        identifier_strings: Flat list of 14 strings (from _build_identifier_strings).
        peoplecode: The PeopleCode source text.
        label: Human-readable label for this staged entry.

    Returns:
        Confirmation message with queue position.
    """
    global _STAGE_COUNTER
    with _API_LOCK:
        _STAGE_COUNTER += 1
        _STAGED_PEOPLECODE.append((identifier_strings, peoplecode, label))
        return (
            f"Staged #{_STAGE_COUNTER}: {label}. "
            f"{len(_STAGED_PEOPLECODE)} item(s) in queue. "
            f"Call save_all_peoplecode() when all changes are staged."
        )


def stage_record_peoplecode(record: str, field: str, event: str, peoplecode_file: str) -> str:
    """Stage PeopleCode for a record field event. Changes are NOT saved until save_all_peoplecode is called.

    This reads PeopleCode from the specified file and stages it for bulk
    compilation and save. Call save_all_peoplecode() after staging all changes.

    Args:
        record: Name of the record (e.g., 'JOB').
        field: Field name within the record (e.g., 'EMPLID').
        event: Event name (e.g., 'FieldChange', 'SaveEdit').
        peoplecode_file: Absolute path to a file containing the PeopleCode source text.

    Returns:
        Confirmation that the change has been staged with queue position.
    """
    peoplecode = _read_peoplecode_file(peoplecode_file)
    identifier = _build_identifier_strings([(1, record), (2, field), (12, event)])
    return _stage_peoplecode(identifier, peoplecode, f"Record.{record}.{field}.{event}")


def stage_page_peoplecode(page: str, peoplecode_file: str) -> str:
    """Stage PeopleCode for a page Activate event. Changes are NOT saved until save_all_peoplecode is called.

    This reads PeopleCode from the specified file and stages it for bulk
    compilation and save. Call save_all_peoplecode() after staging all changes.

    Args:
        page: Page name (e.g., 'JOB_DATA1').
        peoplecode_file: Absolute path to a file containing the PeopleCode source text.

    Returns:
        Confirmation that the change has been staged with queue position.
    """
    peoplecode = _read_peoplecode_file(peoplecode_file)
    identifier = _build_identifier_strings([(9, page), (12, "Activate")])
    return _stage_peoplecode(identifier, peoplecode, f"Page.{page}.Activate")


def stage_page_field_peoplecode(page: str, page_field: str, event: str, peoplecode_file: str) -> str:
    """Stage PeopleCode for a page field event. Changes are NOT saved until save_all_peoplecode is called.

    This reads PeopleCode from the specified file and stages it for bulk
    compilation and save. Call save_all_peoplecode() after staging all changes.

    Args:
        page: Page name.
        page_field: Field name on the page.
        event: Event name.
        peoplecode_file: Absolute path to a file containing the PeopleCode source text.

    Returns:
        Confirmation that the change has been staged with queue position.
    """
    peoplecode = _read_peoplecode_file(peoplecode_file)
    identifier = _build_identifier_strings([(9, page), (67, page_field), (12, event)])
    return _stage_peoplecode(identifier, peoplecode, f"Page.{page}.{page_field}.{event}")


def stage_component_peoplecode(component: str, market: str, event: str, peoplecode_file: str) -> str:
    """Stage PeopleCode for a component event. Changes are NOT saved until save_all_peoplecode is called.

    This reads PeopleCode from the specified file and stages it for bulk
    compilation and save. Call save_all_peoplecode() after staging all changes.

    Args:
        component: Component name.
        market: Market code.
        event: Component-level event name.
        peoplecode_file: Absolute path to a file containing the PeopleCode source text.

    Returns:
        Confirmation that the change has been staged with queue position.
    """
    peoplecode = _read_peoplecode_file(peoplecode_file)
    identifier = _build_identifier_strings([(10, component), (39, market), (12, event)])
    return _stage_peoplecode(identifier, peoplecode, f"Component.{component}.{market}.{event}")


def stage_component_record_peoplecode(component: str, market: str, record: str, event: str, peoplecode_file: str) -> str:
    """Stage PeopleCode for a record within a component. Changes are NOT saved until save_all_peoplecode is called.

    This reads PeopleCode from the specified file and stages it for bulk
    compilation and save. Call save_all_peoplecode() after staging all changes.

    Args:
        component: Component name.
        market: Market code.
        record: Record name used within the component.
        event: Event name.
        peoplecode_file: Absolute path to a file containing the PeopleCode source text.

    Returns:
        Confirmation that the change has been staged with queue position.
    """
    peoplecode = _read_peoplecode_file(peoplecode_file)
    identifier = _build_identifier_strings([(10, component), (39, market), (1, record), (12, event)])
    return _stage_peoplecode(identifier, peoplecode, f"Component.{component}.{market}.{record}.{event}")


def stage_component_record_field_peoplecode(component: str, market: str, record: str, field: str, event: str, peoplecode_file: str) -> str:
    """Stage PeopleCode for a field on a record within a component. Changes are NOT saved until save_all_peoplecode is called.

    This reads PeopleCode from the specified file and stages it for bulk
    compilation and save. Call save_all_peoplecode() after staging all changes.

    Args:
        component: Component name.
        market: Market code.
        record: Record name within the component.
        field: Field name within the record.
        event: Field-level event.
        peoplecode_file: Absolute path to a file containing the PeopleCode source text.

    Returns:
        Confirmation that the change has been staged with queue position.
    """
    peoplecode = _read_peoplecode_file(peoplecode_file)
    identifier = _build_identifier_strings([(10, component), (39, market), (1, record), (2, field), (12, event)])
    return _stage_peoplecode(identifier, peoplecode, f"Component.{component}.{market}.{record}.{field}.{event}")


def stage_application_class_peoplecode(application_class: str, application_package: str, peoplecode_file: str, subpackage1: str = "", subpackage2: str = "") -> str:
    """Stage PeopleCode for an Application Class. Changes are NOT saved until save_all_peoplecode is called.

    This reads PeopleCode from the specified file and stages it for bulk
    compilation and save. Call save_all_peoplecode() after staging all changes.

    Args:
        application_class: Class name containing PeopleCode (e.g., 'MY_CLASS').
        application_package: Top-level application package (e.g., 'MY_APP_PKG').
        peoplecode_file: Absolute path to a file containing the PeopleCode source text.
        subpackage1: (Optional) First-level subpackage within the application package.
        subpackage2: (Optional) Second-level subpackage.

    Returns:
        Confirmation that the change has been staged with queue position.
    """
    peoplecode = _read_peoplecode_file(peoplecode_file)

    pairs = []
    if application_package:
        pairs.append((104, application_package))
    if subpackage1:
        pairs.append((105, subpackage1))
    if subpackage2:
        pairs.append((106, subpackage2))
    if application_class:
        pairs.append((107, application_class))
    pairs.append((12, "OnExecute"))

    identifier = _build_identifier_strings(pairs)

    label_parts = [application_package]
    if subpackage1:
        label_parts.append(subpackage1)
    if subpackage2:
        label_parts.append(subpackage2)
    label_parts.append(application_class)
    label = f"AppClass.{':'.join(label_parts)}.OnExecute"

    return _stage_peoplecode(identifier, peoplecode, label)


def stage_app_engine_peoplecode(ae_program: str, section: str, market: str, step: str, peoplecode_file: str, dbtype: str = "default", effdt: str = "1900-01-01") -> str:
    """Stage PeopleCode for an Application Engine step. Changes are NOT saved until save_all_peoplecode is called.

    This reads PeopleCode from the specified file and stages it for bulk
    compilation and save. Call save_all_peoplecode() after staging all changes.

    Args:
        ae_program: The Application Engine program name.
        section: The section name within the AE program.
        market: The market code associated with the AE program.
        step: The step name within the section.
        peoplecode_file: Absolute path to a file containing the PeopleCode source text.
        dbtype: The database type or AE DB context.
        effdt: The effective date for the AE.

    Returns:
        Confirmation that the change has been staged with queue position.
    """
    peoplecode = _read_peoplecode_file(peoplecode_file)
    identifier = _build_identifier_strings([
        (66, ae_program), (77, section), (39, market),
        (20, dbtype), (21, effdt), (78, step), (12, "OnExecute")
    ])
    return _stage_peoplecode(identifier, peoplecode, f"AppEngine.{ae_program}.{section}.{step}.{market}")


# --- MCP Tools: Stage Management ---

def list_staged_peoplecode() -> str:
    """List all PeopleCode changes currently staged for saving.

    Use this to review what will be compiled and saved when
    save_all_peoplecode() is called.

    Returns:
        A numbered list of staged PeopleCode changes, or a message
        indicating the staging queue is empty.
    """
    with _API_LOCK:
        if not _STAGED_PEOPLECODE:
            return "No PeopleCode changes are currently staged."

        lines = [f"Staged PeopleCode changes ({len(_STAGED_PEOPLECODE)} item(s)):"]
        for i, (_, _, label) in enumerate(_STAGED_PEOPLECODE, 1):
            lines.append(f"  {i}. {label}")
        lines.append("\nCall save_all_peoplecode() to compile and save all staged changes.")
        return "\n".join(lines)


def clear_staged_peoplecode() -> str:
    """Discard all staged PeopleCode changes without saving.

    Use this to reset the staging queue if you want to start over
    or abandon pending changes.

    Returns:
        Confirmation that the staging queue has been cleared.
    """
    global _STAGE_COUNTER
    with _API_LOCK:
        count = len(_STAGED_PEOPLECODE)
        _STAGED_PEOPLECODE.clear()
        _STAGE_COUNTER = 0
    return f"Cleared {count} staged PeopleCode change(s). Staging queue is now empty."


def save_all_peoplecode() -> str:
    """Compile and save ALL staged PeopleCode changes to the database.

    This uses the bulk save API (UpdateMultiPeopleCodeData) which automatically
    handles dependency ordering between PeopleCode programs. All staged changes
    are submitted together in a single operation.

    Call this after staging all your changes with the save_*_peoplecode tools.
    The staging queue is cleared after a successful save.

    Returns:
        Result message from the API indicating compilation and save
        success or errors for each staged item.
    """
    global _STAGE_COUNTER

    with _API_LOCK:
        if not _STAGED_PEOPLECODE:
            return "No PeopleCode changes staged. Use the save_*_peoplecode tools to stage changes first."

        flat_items: List[str] = []
        code_list: List[str] = []

        for identifier_strings, peoplecode, _ in _STAGED_PEOPLECODE:
            flat_items.extend(identifier_strings)
            code_list.append(peoplecode)

        items_arr = (ctypes.c_wchar_p * len(flat_items))(*flat_items)
        code_arr = (ctypes.c_wchar_p * len(code_list))(*code_list)

        api = get_psaiapi()
        result_ptr = api.UpdateMultiPeopleCodeData(items_arr, len(flat_items), code_arr, len(code_list))

        try:
            result = ctypes.wstring_at(result_ptr) if result_ptr else "No response from UpdateMultiPeopleCodeData API."
        finally:
            if result_ptr:
                api.FreePCodeDetails(result_ptr)

        saved_count = len(_STAGED_PEOPLECODE)
        _STAGED_PEOPLECODE.clear()
        _STAGE_COUNTER = 0

    return f"Bulk save completed for {saved_count} item(s).\n\nAPI Response:\n{result}"


# --- Initialization ---

def initialize_server() -> None:
    """
    Initialize the PeopleSoft API server.
    
    This function performs all initialization steps including:
    - Validating environment variables
    - Setting up system paths
    - Reading registry values
    - Parsing TNS configuration
    - Loading and configuring the PSAIAPI DLL
    - Prompting for user password via UI
    - Authenticating to the database
    
    Raises:
        EnvironmentError: If required environment variables or registry values are missing.
        RuntimeError: If DLL loading or database authentication fails.
        FileNotFoundError: If tnsnames.ora is not found.
        ValueError: If TNS configuration is invalid.
    """
    global _PSAIAPI

    if _PSAIAPI is not None:
        return

    with _INIT_LOCK:
        if _PSAIAPI is not None:
            return

        _set_auth_state(_AUTH_PENDING)

        ps_home, oracle_home, tns_admin = validate_environment_variables()
        setup_system_path(ps_home, oracle_home)

        registry_values = read_registry_values()
        validate_registry_values(registry_values)

        db_name = registry_values["DBName"]
        db_type = registry_values["DBType"]
        user_id = registry_values["UserId"]
        acc_id = registry_values["ConnectIdStd"]
        acc_pwd = registry_values["ClientConnectPswdV2Std"]
        owner_id = registry_values["UserId"]

        db_host, _, _ = parse_tnsnames(tns_admin, db_name)

        user_pwd = os.getenv("USER_PASSWORD","") or get_user_password(db_name, user_id, db_type)

        psaiapi = load_psaiapi_dll(ps_home)
        configure_dll_functions(psaiapi)

        try:
            authenticate_database(
                psaiapi, ps_home, db_name, db_type, db_host,
                user_id, user_pwd, acc_id, acc_pwd, owner_id
            )
        finally:
            del user_pwd

        _PSAIAPI = psaiapi
        _set_auth_state(_AUTH_READY)


def sign_off_database() -> None:
    """
    Sign off from the PeopleSoft database.
    
    This function is idempotent — safe to call multiple times.
    Only the first call performs the actual sign-off; subsequent
    calls are no-ops.
    """
    global _SIGNED_OFF
    with _API_LOCK:
        if _SIGNED_OFF or _PSAIAPI is None:
            return
        _SIGNED_OFF = True
        try:
            _PSAIAPI.SignOffDatabase()
        except Exception:
            pass  # Best-effort during shutdown


def _signal_handler(signum: int, frame: object) -> None:
    """Handle termination signals by signing off and exiting."""
    sign_off_database()
    sys.exit(0)


def _warmup_server() -> None:
    """Initialize PeopleSoft in the background."""
    try:
        initialize_server()
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        _set_auth_state(_AUTH_FAILED, error)
        os._exit(1)


# --- Entry Point ---

def main() -> None:
    """
    Main entry point for the MCP server.
    
    Runs the MCP application and warms up PeopleSoft in the background.
    """
    atexit.register(sign_off_database)
    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGBREAK, _signal_handler)

    if _COMPILE_MODE:
        mcp.tool()(compile_record_peoplecode)
        mcp.tool()(compile_page_peoplecode)
        mcp.tool()(compile_page_field_peoplecode)
        mcp.tool()(compile_component_peoplecode)
        mcp.tool()(compile_component_record_peoplecode)
        mcp.tool()(compile_component_record_field_peoplecode)
        mcp.tool()(compile_application_class_peoplecode)
        mcp.tool()(compile_app_engine_peoplecode)

    if _WRITE_MODE:
        mcp.tool()(stage_record_peoplecode)
        mcp.tool()(stage_page_peoplecode)
        mcp.tool()(stage_page_field_peoplecode)
        mcp.tool()(stage_component_peoplecode)
        mcp.tool()(stage_component_record_peoplecode)
        mcp.tool()(stage_component_record_field_peoplecode)
        mcp.tool()(stage_application_class_peoplecode)
        mcp.tool()(stage_app_engine_peoplecode)
        mcp.tool()(list_staged_peoplecode)
        mcp.tool()(clear_staged_peoplecode)
        mcp.tool()(save_all_peoplecode)

    warmup_timer = threading.Timer(_WARMUP_DELAY_SECONDS, _warmup_server)
    warmup_timer.daemon = True
    warmup_timer.start()
    mcp.run()


if __name__ == "__main__":
    main()
