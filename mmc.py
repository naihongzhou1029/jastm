#!/usr/bin/env python3
"""mmc.py - Multi-Monitor Configurator for Windows 10 IoT"""

import argparse
import configparser
import ctypes
import ctypes.wintypes
import sys

if sys.platform != 'win32':
    sys.exit("Error: mmc.py requires Windows.")

# -- Windows API constants -----------------------------------------------------

CCHDEVICENAME = 32
CCHFORMNAME   = 32

DM_POSITION         = 0x00000020
DM_BITSPERPEL       = 0x00040000
DM_PELSWIDTH        = 0x00080000
DM_PELSHEIGHT       = 0x00100000
DM_DISPLAYFREQUENCY = 0x00400000

CDS_UPDATEREGISTRY  = 0x00000001
CDS_SET_PRIMARY     = 0x00000010
CDS_NORESET         = 0x10000000

DISP_CHANGE_SUCCESSFUL = 0
DISP_CHANGE_RESTART    = 1

DISPLAY_DEVICE_ATTACHED_TO_DESKTOP = 0x00000001

MONITORINFOF_PRIMARY = 0x00000001

GWL_STYLE   = -16
GWL_EXSTYLE = -20
WS_MINIMIZE  = 0x20000000
WS_MAXIMIZE  = 0x01000000
WS_EX_TOOLWINDOW = 0x00000080

SW_SHOWNORMAL = 1

MONITOR_DEFAULTTONEAREST = 0x00000002

SW_RESTORE       = 9

# SetDisplayConfig flags (used for non-extend topologies)
SDC_TOPOLOGY_INTERNAL = 0x00000001
SDC_TOPOLOGY_CLONE    = 0x00000002
SDC_TOPOLOGY_EXTEND   = 0x00000004
SDC_TOPOLOGY_EXTERNAL = 0x00000008
SDC_APPLY             = 0x00000080
SDC_SAVE_TO_DATABASE  = 0x00000200

_TOPOLOGY_SDC_FLAGS = {
    'clone':    SDC_TOPOLOGY_CLONE,
    'internal': SDC_TOPOLOGY_INTERNAL,
    'external': SDC_TOPOLOGY_EXTERNAL,
}
SWP_NOZORDER     = 0x0004
SWP_NOACTIVATE   = 0x0010
HWND_TOP         = 0

# Window classes that belong to the shell - never move these.
_SKIP_CLASSES = frozenset({
    'Shell_TrayWnd', 'Shell_SecondaryTrayWnd',
    'Progman', 'WorkerW', 'DV2ControlHost',
})

# -- ctypes structures ---------------------------------------------------------

class POINTL(ctypes.Structure):
    _fields_ = [('x', ctypes.c_long), ('y', ctypes.c_long)]


class _DevmodePrinter(ctypes.Structure):
    """Printer-specific union arm of DEVMODE (16 bytes, 8 shorts)."""
    _fields_ = [
        ('dmOrientation',   ctypes.c_short),
        ('dmPaperSize',     ctypes.c_short),
        ('dmPaperLength',   ctypes.c_short),
        ('dmPaperWidth',    ctypes.c_short),
        ('dmScale',         ctypes.c_short),
        ('dmCopies',        ctypes.c_short),
        ('dmDefaultSource', ctypes.c_short),
        ('dmPrintQuality',  ctypes.c_short),
    ]


class _DevmodeDisplay(ctypes.Structure):
    """Display-specific union arm of DEVMODE (16 bytes: POINTL + 2 DWORDs)."""
    _fields_ = [
        ('dmPosition',           POINTL),
        ('dmDisplayOrientation', ctypes.c_uint32),
        ('dmDisplayFixedOutput', ctypes.c_uint32),
    ]


class _DevmodeUnion(ctypes.Union):
    _fields_ = [
        ('printer', _DevmodePrinter),
        ('display', _DevmodeDisplay),
    ]


class DEVMODE(ctypes.Structure):
    _anonymous_ = ('_u',)
    _fields_ = [
        ('dmDeviceName',       ctypes.c_wchar * CCHDEVICENAME),
        ('dmSpecVersion',      ctypes.c_uint16),
        ('dmDriverVersion',    ctypes.c_uint16),
        ('dmSize',             ctypes.c_uint16),
        ('dmDriverExtra',      ctypes.c_uint16),
        ('dmFields',           ctypes.c_uint32),
        ('_u',                 _DevmodeUnion),
        ('dmColor',            ctypes.c_short),
        ('dmDuplex',           ctypes.c_short),
        ('dmYResolution',      ctypes.c_short),
        ('dmTTOption',         ctypes.c_short),
        ('dmCollate',          ctypes.c_short),
        ('dmFormName',         ctypes.c_wchar * CCHFORMNAME),
        ('dmLogPixels',        ctypes.c_uint16),
        ('dmBitsPerPel',       ctypes.c_uint32),
        ('dmPelsWidth',        ctypes.c_uint32),
        ('dmPelsHeight',       ctypes.c_uint32),
        ('dmDisplayFlags',     ctypes.c_uint32),
        ('dmDisplayFrequency', ctypes.c_uint32),
        ('dmICMMethod',        ctypes.c_uint32),
        ('dmICMIntent',        ctypes.c_uint32),
        ('dmMediaType',        ctypes.c_uint32),
        ('dmDitherType',       ctypes.c_uint32),
        ('dmReserved1',        ctypes.c_uint32),
        ('dmReserved2',        ctypes.c_uint32),
        ('dmPanningWidth',     ctypes.c_uint32),
        ('dmPanningHeight',    ctypes.c_uint32),
    ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.dmSize = ctypes.sizeof(self)


class DISPLAY_DEVICE(ctypes.Structure):
    _fields_ = [
        ('cb',           ctypes.c_uint32),
        ('DeviceName',   ctypes.c_wchar * 32),
        ('DeviceString', ctypes.c_wchar * 128),
        ('StateFlags',   ctypes.c_uint32),
        ('DeviceID',     ctypes.c_wchar * 128),
        ('DeviceKey',    ctypes.c_wchar * 128),
    ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.cb = ctypes.sizeof(self)


class RECT(ctypes.Structure):
    _fields_ = [
        ('left',   ctypes.c_long),
        ('top',    ctypes.c_long),
        ('right',  ctypes.c_long),
        ('bottom', ctypes.c_long),
    ]


class MONITORINFO(ctypes.Structure):
    _fields_ = [
        ('cbSize',    ctypes.c_uint32),
        ('rcMonitor', RECT),
        ('rcWork',    RECT),
        ('dwFlags',   ctypes.c_uint32),
    ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.cbSize = ctypes.sizeof(self)


class WINDOWPLACEMENT(ctypes.Structure):
    _fields_ = [
        ('length',           ctypes.c_uint32),
        ('flags',            ctypes.c_uint32),
        ('showCmd',          ctypes.c_uint32),
        ('ptMinPosition',    ctypes.wintypes.POINT),
        ('ptMaxPosition',    ctypes.wintypes.POINT),
        ('rcNormalPosition', RECT),
    ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.length = ctypes.sizeof(self)


# -- Display helpers -----------------------------------------------------------

_user32 = ctypes.windll.user32

# -- Declare argtypes / restype for handle-returning functions -----------------
# Without these, ctypes defaults to c_int (32-bit) which truncates 64-bit
# handles on x64 Windows, silently breaking calls like GetMonitorInfoW.

_user32.MonitorFromWindow.argtypes = [ctypes.wintypes.HWND, ctypes.c_uint32]
_user32.MonitorFromWindow.restype  = ctypes.wintypes.HANDLE

_user32.GetMonitorInfoW.argtypes = [ctypes.wintypes.HANDLE, ctypes.POINTER(MONITORINFO)]
_user32.GetMonitorInfoW.restype  = ctypes.c_bool


def _enumerate_active_displays():
    """Return list of DeviceName strings for all desktop-attached adapters."""
    displays = []
    i = 0
    while True:
        dd = DISPLAY_DEVICE()
        if not _user32.EnumDisplayDevicesW(None, i, ctypes.byref(dd), 0):
            break
        if dd.StateFlags & DISPLAY_DEVICE_ATTACHED_TO_DESKTOP:
            displays.append(dd.DeviceName)
        i += 1
    return displays


def _enumerate_modes(device_name):
    """Return sorted list of unique (width, height, freq) tuples, descending by area then freq."""
    seen = set()
    i = 0
    while True:
        dm = DEVMODE()
        if not _user32.EnumDisplaySettingsExW(device_name, i, ctypes.byref(dm), 0):
            break
        seen.add((dm.dmPelsWidth, dm.dmPelsHeight, dm.dmDisplayFrequency))
        i += 1
    return sorted(seen, key=lambda m: (m[0] * m[1], m[2]), reverse=True)


def _find_best_mode(modes, target_w, target_h, target_freq):
    """
    Return the (w, h, freq) that best matches the target resolution and refresh rate.

    Priority:
      1. Exact match.
      2. Same resolution, highest refresh rate <= target.
      3. Largest resolution below target, highest refresh rate <= target.
      4. Absolute fallback: highest available mode.
    """
    mode_set = set(modes)

    # 1. Exact
    if (target_w, target_h, target_freq) in mode_set:
        return (target_w, target_h, target_freq)

    # 2. Same resolution, lower-or-equal frequency
    same_res = [(w, h, f) for w, h, f in modes
                if w == target_w and h == target_h and f <= target_freq]
    if same_res:
        return max(same_res, key=lambda m: m[2])

    # 3. Smaller resolution, lower-or-equal frequency
    smaller = [(w, h, f) for w, h, f in modes
               if w * h < target_w * target_h and f <= target_freq]
    if smaller:
        return max(smaller, key=lambda m: (m[0] * m[1], m[2]))

    # 4. Absolute fallback
    return modes[0] if modes else None


def _apply_one(device_name, w, h, freq, pos_x, pos_y, is_primary):
    """Stage a single display change (CDS_NORESET). Returns the API result code."""
    dm = DEVMODE()
    dm.dmFields = DM_PELSWIDTH | DM_PELSHEIGHT | DM_DISPLAYFREQUENCY | DM_POSITION
    dm.dmPelsWidth  = w
    dm.dmPelsHeight = h
    dm.dmDisplayFrequency = freq
    dm.display.dmPosition.x = pos_x
    dm.display.dmPosition.y = pos_y

    flags = CDS_UPDATEREGISTRY | CDS_NORESET
    if is_primary:
        flags |= CDS_SET_PRIMARY

    return _user32.ChangeDisplaySettingsExW(
        device_name, ctypes.byref(dm), None, flags, None
    )


def _commit():
    """Flush all staged display changes to the system."""
    return _user32.ChangeDisplaySettingsExW(None, None, None, 0, None)


# -- Window-moving helpers -----------------------------------------------------

_EnumWindowsProc = ctypes.WINFUNCTYPE(
    ctypes.c_bool,
    ctypes.wintypes.HWND,
    ctypes.wintypes.LPARAM,
)

_MonitorEnumProc = ctypes.WINFUNCTYPE(
    ctypes.c_bool,
    ctypes.wintypes.HANDLE,   # HMONITOR
    ctypes.wintypes.HDC,
    ctypes.POINTER(RECT),
    ctypes.wintypes.LPARAM,
)


def _get_primary_rect():
    """Return (left, top, right, bottom) for the current primary monitor."""
    found = [None]

    def _cb(hmon, hdc, lprect, lparam):
        mi = MONITORINFO()
        _user32.GetMonitorInfoW(hmon, ctypes.byref(mi))
        if mi.dwFlags & MONITORINFOF_PRIMARY:
            r = mi.rcMonitor
            found[0] = (r.left, r.top, r.right, r.bottom)
        return True

    _user32.EnumDisplayMonitors(None, None, _MonitorEnumProc(_cb), 0)
    return found[0] or (0, 0, 3840, 2160)


_SW_NAMES = {1: 'normal', 2: 'minimised', 3: 'maximised'}


def _get_window_title(hwnd):
    """Return the window title (up to 200 chars) or '<no title>'."""
    buf = ctypes.create_unicode_buffer(201)
    _user32.GetWindowTextW(hwnd, buf, 201)
    return buf.value or '<no title>'


def _collect_candidate_windows(verbose=False):
    """Enumerate visible top-level windows, skipping shell and tool windows.

    Returns a list of (hwnd, title, cls_name, state_str) tuples and a list of
    diagnostic skip-rows for verbose output.
    """
    candidates = []
    skip_rows = []

    def _cb(hwnd, _):
        if not _user32.IsWindowVisible(hwnd):
            return True

        title = _get_window_title(hwnd) if verbose else ''

        ex_style = _user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        if ex_style & WS_EX_TOOLWINDOW:
            if verbose:
                skip_rows.append((title, '', '', '', 'skip:tool-window'))
            return True

        buf = ctypes.create_unicode_buffer(256)
        _user32.GetClassNameW(hwnd, buf, 256)
        cls_name = buf.value
        if cls_name in _SKIP_CLASSES:
            if verbose:
                skip_rows.append((title, cls_name, '', '', 'skip:shell'))
            return True

        wp = WINDOWPLACEMENT()
        if not _user32.GetWindowPlacement(hwnd, ctypes.byref(wp)):
            if verbose:
                skip_rows.append((title, cls_name, '', '', 'skip:no-placement'))
            return True

        state = _SW_NAMES.get(wp.showCmd, f'showCmd={wp.showCmd}')
        candidates.append((hwnd, title, cls_name, state))
        return True

    _user32.EnumWindows(_EnumWindowsProc(_cb), 0)
    return candidates, skip_rows


def _move_windows_to_target(target_rect, verbose=False):
    """Move every visible top-level window onto the given target rectangle.

    *target_rect* is ``(left, top, right, bottom)`` in virtual-desktop
    coordinates — typically the position and size of the monitor where the
    user wants windows to appear.

    Every candidate window is restored from minimised / maximised state,
    shrunk to 100x100, placed at the centre of *target_rect* via
    ``SetWindowPos``.

    When *verbose* is True, prints a diagnostic table of every visible
    top-level window: title, class, state, position, and action taken.
    """
    pl, pt, pr, pb = target_rect
    pw = max(pr - pl, 1)
    ph = max(pb - pt, 1)

    if verbose:
        print(f"  Target monitor rect: ({pl}, {pt}) - ({pr}, {pb})  "
              f"[{pw}x{ph}]")

    candidates, skip_rows = _collect_candidate_windows(verbose)

    moved = 0
    diag_rows = []   # (title, cls, state, rect_str, action)
    swp_flags = SWP_NOZORDER | SWP_NOACTIVATE

    # Target: 80% of 1280x720 (= 1024x576), centred on the target monitor.
    tgt_w = min(1024, pw)
    tgt_h = min(576, ph)
    tgt_x = pl + (pw - tgt_w) // 2
    tgt_y = pt + (ph - tgt_h) // 2

    for hwnd, title, cls_name, state in candidates:
        # Read current rect for diagnostics.
        wp = WINDOWPLACEMENT()
        _user32.GetWindowPlacement(hwnd, ctypes.byref(wp))
        rc = wp.rcNormalPosition
        rect_str = f'({rc.left},{rc.top})-({rc.right},{rc.bottom})'

        # Force the window into normal (restored) state before repositioning.
        # ShowWindow(SW_RESTORE) alone isn't always enough — check the live
        # style bits and keep calling until WS_MINIMIZE / WS_MAXIMIZE are gone.
        _user32.ShowWindow(hwnd, SW_RESTORE)
        _user32.ShowWindow(hwnd, SW_SHOWNORMAL)
        style = _user32.GetWindowLongW(hwnd, GWL_STYLE)
        if style & (WS_MINIMIZE | WS_MAXIMIZE):
            # Belt-and-suspenders: directly clear the bits and reapply.
            _user32.SetWindowLongW(
                hwnd, GWL_STYLE, style & ~(WS_MINIMIZE | WS_MAXIMIZE)
            )

        ok = _user32.SetWindowPos(
            hwnd, HWND_TOP, tgt_x, tgt_y, tgt_w, tgt_h, swp_flags
        )

        new_rect = f'({tgt_x},{tgt_y})-({tgt_x+tgt_w},{tgt_y+tgt_h})'
        if verbose:
            # Re-read state after restore to show the actual current state.
            wp2 = WINDOWPLACEMENT()
            _user32.GetWindowPlacement(hwnd, ctypes.byref(wp2))
            after_state = _SW_NAMES.get(wp2.showCmd, f'showCmd={wp2.showCmd}')
            action = (f'{state} -> {after_state}  '
                      f'{rect_str} -> {new_rect}  [ok={ok}]')
            diag_rows.append((title, cls_name, after_state, rect_str, action))
        moved += 1

    # -- Verbose output --------------------------------------------------------
    if verbose:
        all_rows = skip_rows + diag_rows
        print(f"\n  Window census: {len(candidates)} candidate(s)  "
              f"({moved} moved, {len(skip_rows)} skipped)")
        if all_rows:
            max_t = 40
            max_c = 28
            print(f"  {'Title':<{max_t}}  {'Class':<{max_c}}  "
                  f"{'State':<10}  Action")
            print(f"  {'-'*max_t}  {'-'*max_c}  {'-'*10}  {'-'*50}")
            for row_title, cls, st, _, action in all_rows:
                t = (row_title[:max_t-1] + '..') if len(row_title) > max_t else row_title
                c = (cls[:max_c-1] + '..') if len(cls) > max_c else cls
                line = (f"  {t:<{max_t}}  {c:<{max_c}}  "
                        f"{st:<10}  {action}")
                print(line.encode(sys.stdout.encoding or 'utf-8',
                                  errors='replace').decode(
                                  sys.stdout.encoding or 'utf-8',
                                  errors='replace'))

    return moved


# -- Topology helpers ----------------------------------------------------------

def _apply_topology(topology):
    """Switch the display topology via SetDisplayConfig (non-extend modes only).

    *topology* must be one of 'clone', 'internal', or 'external'.
    Returns the LONG result code (0 = ERROR_SUCCESS).
    """
    _user32.SetDisplayConfig.restype  = ctypes.c_long
    _user32.SetDisplayConfig.argtypes = [
        ctypes.c_uint32, ctypes.c_void_p,
        ctypes.c_uint32, ctypes.c_void_p,
        ctypes.c_uint32,
    ]
    flags = _TOPOLOGY_SDC_FLAGS[topology] | SDC_APPLY | SDC_SAVE_TO_DATABASE
    return _user32.SetDisplayConfig(0, None, 0, None, flags)


# -- Config parsing ------------------------------------------------------------

_VALID_TOPOLOGIES = frozenset({'extend', 'clone', 'internal', 'external'})


def _parse_config(path):
    """
    Parse an INI file with an optional [display] section and [monitor1],
    [monitor2], ... sections.

    Returns ``(monitors, topology)`` where *monitors* is a list of dicts
    (width, height, freq, primary, move_windows_to) ordered by section name,
    and *topology* is one of 'extend' (default), 'clone', 'internal',
    'external'.  Exits with an error message on any malformed value.
    """
    cp = configparser.ConfigParser()
    if not cp.read(path):
        sys.exit(f"Error: Config file not found: {path}")

    # -- [display] section -----------------------------------------------------
    topology = 'extend'
    if cp.has_section('display'):
        raw_topo = cp['display'].get('topology', 'extend').strip().lower()
        if raw_topo not in _VALID_TOPOLOGIES:
            sys.exit(
                f"Error: [display] topology '{raw_topo}' must be one of: "
                + ', '.join(sorted(_VALID_TOPOLOGIES))
            )
        topology = raw_topo

    # -- [monitorN] sections ---------------------------------------------------
    monitors = []
    for section in sorted(cp.sections()):
        if not section.lower().startswith('monitor'):
            continue
        raw_res = cp[section].get('resolution', '1920x1080').strip()
        try:
            w_str, h_str = raw_res.lower().split('x')
            w, h = int(w_str), int(h_str)
        except ValueError:
            sys.exit(f"Error: [{section}] resolution '{raw_res}' must be WIDTHxHEIGHT (e.g. 1920x1080)")
        try:
            freq = int(cp[section].get('refresh_rate', '60').strip())
        except ValueError:
            sys.exit(f"Error: [{section}] refresh_rate must be an integer")
        primary = cp[section].getboolean('primary', fallback=False)
        move_to = cp[section].getboolean('move_windows_to', fallback=False)
        monitors.append({'width': w, 'height': h, 'freq': freq,
                         'primary': primary, 'move_windows_to': move_to})

    return monitors, topology


# -- Entry point ---------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        prog='mmc.py',
        description='Multi-Monitor Configurator for Windows 10 IoT',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            'Example:\n'
            '  python mmc.py --config-file mmc.ini\n\n'
            'The INI file must contain one [monitorN] section per display.\n'
            'Exactly one section must have primary = true.\n'
            'If a target resolution or refresh rate is unavailable, the script\n'
            'automatically downgrades to the closest supported mode.\n'
        ),
    )
    parser.add_argument(
        '--config-file', metavar='FILE', required=True,
        help='Path to an INI file describing the desired monitor layout',
    )
    parser.add_argument(
        '--verbose', action='store_true', default=False,
        help='Print a diagnostic table of every window considered during the move step',
    )

    if len(sys.argv) == 1:
        parser.print_help()
        sys.exit(0)

    args = parser.parse_args()

    # -- Load and validate config ----------------------------------------------
    monitors, topology = _parse_config(args.config_file)
    if not monitors and topology == 'extend':
        sys.exit(f"Error: No [monitor*] sections found in {args.config_file!r}")

    # -- Non-extend topologies: delegate entirely to SetDisplayConfig ----------
    if topology != 'extend':
        print(f"Applying display topology: {topology}...")
        code = _apply_topology(topology)
        ok   = (code == 0)
        print(f"  SetDisplayConfig '{topology}': "
              f"{'OK' if ok else f'FAILED (code {code})'}")

        print("\nMoving running windows to primary monitor...")
        n = _move_windows_to_target(_get_primary_rect(), verbose=args.verbose)
        print(f"Moved {n} window(s).")
        print("Done.")
        sys.exit(0 if ok else 1)

    # -- Extend topology: validate [monitorN] sections -------------------------
    n_primary = sum(1 for m in monitors if m['primary'])
    if n_primary != 1:
        sys.exit(
            f"Error: Exactly one monitor must have primary = true "
            f"(found {n_primary})."
        )

    n_move_to = sum(1 for m in monitors if m['move_windows_to'])
    if n_move_to > 1:
        sys.exit(
            f"Error: At most one monitor may have move_windows_to = true "
            f"(found {n_move_to})."
        )

    primary_idx = next(i for i, m in enumerate(monitors) if m['primary'])

    # -- Enumerate physical displays -------------------------------------------
    displays = _enumerate_active_displays()
    if not displays:
        sys.exit("Error: No active displays detected.")

    print(f"Detected {len(displays)} active display(s), "
          f"{len(monitors)} config section(s).")

    if len(displays) < len(monitors):
        print(
            f"Warning: Only {len(displays)} display(s) active; "
            f"ignoring last {len(monitors) - len(displays)} config section(s)."
        )
        monitors = monitors[:len(displays)]
        if primary_idx >= len(monitors):
            sys.exit("Error: Primary monitor section is out of range after truncation.")

    # -- Resolve best mode for each display -----------------------------------
    resolved = []
    for i, (device, cfg) in enumerate(zip(displays, monitors)):
        modes = _enumerate_modes(device)
        if not modes:
            sys.exit(f"Error: Could not enumerate modes for display {i + 1} ({device!r})")
        best = _find_best_mode(modes, cfg['width'], cfg['height'], cfg['freq'])
        if best is None:
            sys.exit(f"Error: No usable mode found for display {i + 1} ({device!r})")
        target = (cfg['width'], cfg['height'], cfg['freq'])
        if best == target:
            print(f"  Display {i + 1}: {best[0]}x{best[1]}@{best[2]}Hz  (exact match)")
        else:
            print(
                f"  Display {i + 1}: {target[0]}x{target[1]}@{target[2]}Hz "
                f"not available -> using {best[0]}x{best[1]}@{best[2]}Hz"
            )
        resolved.append({'device': device, 'mode': best, 'primary': cfg['primary'],
                         'move_windows_to': cfg['move_windows_to']})

    # -- Compute monitor positions ---------------------------------------------
    # Primary monitor is placed at the virtual-desktop origin (0, 0).
    # Non-primary monitors are tiled to the right of the primary in config order.
    primary_w = resolved[primary_idx]['mode'][0]
    x_cursor  = primary_w
    for r in resolved:
        if r['primary']:
            r['pos'] = (0, 0)
        else:
            r['pos'] = (x_cursor, 0)
            x_cursor += r['mode'][0]

    # -- Apply display settings ------------------------------------------------
    print("\nApplying display settings...")
    all_ok = True
    for r in resolved:
        w, h, f   = r['mode']
        px, py    = r['pos']
        code      = _apply_one(r['device'], w, h, f, px, py, r['primary'])
        ok        = code in (DISP_CHANGE_SUCCESSFUL, DISP_CHANGE_RESTART)
        result_s  = 'OK' if ok else f'FAILED (code {code})'
        primary_s = '  [PRIMARY]' if r['primary'] else ''
        print(f"  {r['device']}: {w}x{h}@{f}Hz at ({px},{py}){primary_s} -> {result_s}")
        if not ok:
            all_ok = False

    commit_code = _commit()
    if commit_code == DISP_CHANGE_SUCCESSFUL:
        print("Display configuration committed successfully.")
    elif commit_code == DISP_CHANGE_RESTART:
        print("Display configuration committed. A system restart may be required.")
    else:
        print(f"Warning: commit returned code {commit_code}.")
        all_ok = False

    # -- Move windows to target monitor ----------------------------------------
    # Use the monitor marked move_windows_to; fall back to primary.
    move_targets = [r for r in resolved if r['move_windows_to']]
    if move_targets:
        tgt = move_targets[0]
    else:
        tgt = resolved[primary_idx]
    tgt_x, tgt_y = tgt['pos']
    tgt_w, tgt_h = tgt['mode'][0], tgt['mode'][1]
    target_rect = (tgt_x, tgt_y, tgt_x + tgt_w, tgt_y + tgt_h)

    label = 'move_windows_to' if move_targets else 'primary'
    print(f"\nMoving running windows to {label} monitor...")
    n = _move_windows_to_target(target_rect, verbose=args.verbose)
    print(f"Moved {n} window(s).")
    print("Done.")

    sys.exit(0 if all_ok else 1)


if __name__ == '__main__':
    main()
