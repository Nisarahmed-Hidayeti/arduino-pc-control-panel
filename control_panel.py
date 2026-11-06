#!/usr/bin/env python3
"""Local RFID control panel. The password never goes to the ESP8266 or card."""
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import threading
import time

CONFIG = Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')) / 'rfid-pc-panel' / 'config.json'
DEFAULTS = {'port': '', 'uid': '', 'password': 'password'}


def normalize_uid(value):
    value = re.sub(r'[\s:-]', '', value).upper()
    if not re.fullmatch(r'(?:[0-9A-F]{8}|[0-9A-F]{14}|[0-9A-F]{20})', value):
        raise ValueError('Card UID must contain 4, 7, or 10 hexadecimal bytes.')
    return value


def load_config():
    try:
        result = json.loads(CONFIG.read_text())
        return {key: str(result.get(key, default)) for key, default in DEFAULTS.items()}
    except FileNotFoundError:
        return DEFAULTS.copy()


def save_config(config):
    CONFIG.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = CONFIG.with_suffix('.tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as handle:
        json.dump(config, handle, indent=2)
        handle.write('\n')
    temporary.replace(CONFIG)
    CONFIG.chmod(0o600)


def type_password(password):
    # stdin keeps the secret out of process arguments. No shell invocation.
    subprocess.run(['ydotool', 'type', '--file', '-'], input=password,
                   text=True, check=True, timeout=30, capture_output=True)
    subprocess.run(['ydotool', 'key', '28:1', '28:0'],
                   check=True, timeout=5, capture_output=True)


def main():
    import tkinter as tk
    from tkinter import ttk, messagebox
    import serial
    from serial.tools import list_ports

    root = tk.Tk()
    root.title('RFID PC Control Panel — NodeMCU')
    root.geometry('640x470')
    frame = ttk.Frame(root, padding=20)
    frame.pack(fill='both', expand=True)
    try:
        config = load_config()
    except (ValueError, OSError, TypeError) as exc:
        messagebox.showerror('Configuration error', str(exc))
        root.destroy()
        return
    fields = {key: tk.StringVar(value=value) for key, value in config.items()}
    status = tk.StringVar(value='Disconnected. Connect, then enroll a card.')
    armed = tk.BooleanVar(value=False)
    events = queue.Queue()
    stop = threading.Event()
    state = {'thread': None, 'enroll': False, 'busy': False, 'last': 0.0,
             'generation': 0}

    def disarm(*_):
        armed.set(False)
    for variable in fields.values():
        variable.trace_add('write', disarm)

    ttk.Label(frame, text='RFID Password Control', font=('', 19, 'bold')).pack(anchor='w')
    ttk.Label(frame, text='NodeMCU V3 + RC522 • USB serial • Linux / ydotool').pack(anchor='w', pady=(0, 15))
    for key, label in [('port', 'USB serial port'), ('uid', 'Approved card UID'), ('password', 'Password')]:
        ttk.Label(frame, text=label).pack(anchor='w')
        if key == 'port':
            widget = ttk.Combobox(frame, textvariable=fields[key], values=[p.device for p in list_ports.comports()])
        else:
            widget = ttk.Entry(frame, textvariable=fields[key], show='•' if key == 'password' else '')
        widget.pack(fill='x', pady=(2, 8))

    def persist():
        data = {key: var.get() for key, var in fields.items()}
        data['uid'] = normalize_uid(data['uid'])
        if not data['password'] or any(c in data['password'] for c in '\r\n\x00'):
            raise ValueError('Enter a nonempty, single-line password.')
        save_config(data)
        return data

    def save():
        try:
            persist()
            status.set('Saved. Password is stored locally with owner-only permissions.')
        except (ValueError, OSError) as exc:
            messagebox.showerror('Cannot save', str(exc))

    def reader(port, generation):
        try:
            with serial.Serial(port, 115200, timeout=0.3) as device:
                # Ignore boot output and scans made before this connection.
                stop.wait(2)
                device.reset_input_buffer()
                events.put(('status', generation, 'Connected. Scan a card or choose Enroll next card.'))
                while not stop.is_set():
                    line = device.readline(128).decode('ascii', errors='ignore').strip()
                    if line.startswith('CARD:'):
                        try:
                            uid = normalize_uid(line[5:])
                        except ValueError:
                            continue
                        events.put(('card', generation, (uid, time.monotonic())))
                    elif line.startswith('ERROR:'):
                        events.put(('status', generation, 'RC522 not detected. Check wiring and 3.3V power.'))
        except (serial.SerialException, OSError):
            events.put(('status', generation, 'Serial connection failed. Check the port, permissions, and cable.'))
        finally:
            events.put(('closed', generation, None))

    def connect():
        if state['busy']:
            status.set('Wait for the current typing operation to finish.')
            return
        disarm()
        state['enroll'] = False
        if state['thread'] and state['thread'].is_alive():
            stop.set()
            status.set('Disconnecting…')
            return
        stop.clear()
        state['generation'] += 1
        state['thread'] = threading.Thread(target=reader, args=(fields['port'].get(), state['generation']), daemon=True)
        state['thread'].start()
        status.set('Connecting…')

    def enroll():
        disarm()
        if not state['thread'] or not state['thread'].is_alive() or stop.is_set():
            status.set('Connect to the NodeMCU first.')
            return
        state['enroll'] = True
        status.set('Enrollment: scan the replacement card. Then click Save settings.')

    def arm():
        if not armed.get():
            return
        try:
            data = persist()
            if data['password'] == 'password':
                raise ValueError('Replace the "password" placeholder before arming.')
            if not state['thread'] or not state['thread'].is_alive() or stop.is_set():
                raise ValueError('Connect to the NodeMCU first.')
        except (ValueError, OSError) as exc:
            disarm()
            messagebox.showerror('Cannot arm', str(exc))
            return
        state['enroll'] = False
        status.set('Armed. Focus the password field, then scan your card.')

    def output(password, generation):
        try:
            type_password(password)
            events.put(('typed', generation, 'Password typed and Enter pressed.'))
        except (OSError, subprocess.SubprocessError):
            events.put(('failed', generation, 'Typing failed. Check ydotool, its daemon, and socket permissions.'))

    def poll():
        while not events.empty():
            kind, generation, value = events.get_nowait()
            if generation != state['generation']:
                continue
            if kind == 'closed':
                disarm()
                state['enroll'] = False
            elif kind in ('typed', 'failed'):
                state['busy'] = False
                status.set(value)
                if kind == 'failed':
                    disarm()
            elif kind == 'status':
                status.set(value)
            elif kind == 'card':
                uid, scanned = value
                if time.monotonic() - scanned > 1 or stop.is_set():
                    continue
                if state['enroll']:
                    fields['uid'].set(uid)
                    state['enroll'] = False
                    status.set('Card enrolled. Save settings to keep this card.')
                elif not armed.get():
                    status.set('Card detected. Disarmed; no password typed.')
                elif uid != fields['uid'].get().replace(' ', '').replace(':', '').replace('-', '').upper():
                    status.set('Unrecognized card. No password typed.')
                elif not state['busy'] and scanned - state['last'] >= 4:
                    state['busy'] = True
                    state['last'] = scanned
                    threading.Thread(target=output, args=(fields['password'].get(), generation), daemon=True).start()
        root.after(75, poll)

    buttons = ttk.Frame(frame)
    buttons.pack(fill='x', pady=8)
    for label, command in [('Connect / disconnect', connect), ('Enroll next card', enroll), ('Save settings', save)]:
        ttk.Button(buttons, text=label, command=command).pack(side='left', padx=(0, 6))
    ttk.Checkbutton(frame, text='Arm password typing + Enter', variable=armed, command=arm).pack(anchor='w', pady=8)
    ttk.Label(frame, textvariable=status, wraplength=580).pack(anchor='w', pady=8)
    ttk.Label(frame, text='Keep this app running. Scans type into the currently focused field.', wraplength=580).pack(anchor='w')
    def close():
        stop.set()
        disarm()
        root.destroy()
    root.protocol('WM_DELETE_WINDOW', close)
    poll()
    root.mainloop()


if __name__ == '__main__':
    main()
