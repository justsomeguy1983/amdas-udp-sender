"""AMDAS UDP test sender: a small window that sends the AMDAS UDP transmission once a second from a
simulated flight, so a receiver can be developed without the real unit.

  python amdas_udp_sender.py               open the window
  python amdas_udp_sender.py --self-test   check sending and the window work, exit 0 (good) or 1
"""

import argparse
import queue
import socket
import sys
import time
import tkinter as tk
from tkinter import font as tkfont
from tkinter import messagebox, ttk

from amdas import AMDAS_VERSION
from flight import FIX_NAMES, MAX_MINUTES, MIN_MINUTES
from sender import Sender

VERSION = "1.0.0"
TITLE = "AMDAS UDP test sender"
KEEP_LINES = 300
GREEN, RED, GREY = "#1a7f37", "#cf222e", "#57606a"
FIELDS = ("sys_time (seconds since midnight UTC) · pressure (psia) · pressure altitude (ft) · "
          "GPS altitude (m above sea level) · temperature (°C) · humidity (%RH) · "
          "gps_fix (0 none, 2 3D, 3 SBAS; 7-field records only)")


def mmss(seconds):
    return "%d:%02d" % divmod(int(seconds), 60)


class Window:
    def __init__(self, root):
        self.root = root
        self.sender = None
        self.events = queue.Queue()
        root.title("%s %s" % (TITLE, VERSION))
        root.minsize(760, 520)
        root.protocol("WM_DELETE_WINDOW", self.close)
        mono = tkfont.nametofont("TkFixedFont").copy()
        mono.configure(size=10)

        outer = ttk.Frame(root, padding=12)
        outer.pack(fill="both", expand=True)

        settings = ttk.LabelFrame(outer, text="Settings", padding=10)
        settings.pack(fill="x")
        self.host = tk.StringVar(value="127.0.0.1")
        self.port = tk.StringVar(value="5810")
        self.minutes = tk.StringVar(value="20")
        self.seven = tk.BooleanVar(value=True)
        ttk.Label(settings, text="Receiver address").grid(row=0, column=0, sticky="w")
        self.host_entry = ttk.Entry(settings, textvariable=self.host, width=22)
        self.host_entry.grid(row=0, column=1, sticky="w", padx=(6, 18))
        ttk.Label(settings, text="Port").grid(row=0, column=2, sticky="w")
        self.port_entry = ttk.Entry(settings, textvariable=self.port, width=7)
        self.port_entry.grid(row=0, column=3, sticky="w", padx=(6, 18))
        ttk.Label(settings, text="Flight length").grid(row=0, column=4, sticky="w")
        self.minutes_box = ttk.Spinbox(settings, textvariable=self.minutes, from_=MIN_MINUTES,
                                       to=MAX_MINUTES, increment=5, width=5)
        self.minutes_box.grid(row=0, column=5, sticky="w", padx=(6, 4))
        ttk.Label(settings, text="minutes").grid(row=0, column=6, sticky="w")
        self.seven_box = ttk.Checkbutton(
            settings, variable=self.seven,
            text="Send 7 fields, with gps_fix (as AMDAS does). Clear it for the legacy 6 fields.")
        self.seven_box.grid(row=1, column=0, columnspan=7, sticky="w", pady=(8, 0))
        ttk.Label(settings, foreground=GREY, wraplength=700, justify="left",
                  text="127.0.0.1 is this computer: the packets then come from 127.0.0.1, not from the "
                       "real unit's address, so the receiver must accept that source while testing."
                  ).grid(row=2, column=0, columnspan=7, sticky="w", pady=(6, 0))

        buttons = ttk.Frame(outer, padding=(0, 10, 0, 4))
        buttons.pack(fill="x")
        self.start_button = ttk.Button(buttons, text="Start", command=self.start, width=12)
        self.start_button.pack(side="left")
        self.stop_button = ttk.Button(buttons, text="Stop", command=self.stop, width=12, state="disabled")
        self.stop_button.pack(side="left", padx=(8, 0))
        self.state = tk.Label(buttons, text="Stopped", fg=GREY, font=("TkDefaultFont", 11, "bold"))
        self.state.pack(side="left", padx=(16, 0))

        self.counts = ttk.Label(outer, text="")
        self.counts.pack(fill="x")
        self.flight = ttk.Label(outer, text="")
        self.flight.pack(fill="x", pady=(2, 6))

        packets = ttk.LabelFrame(outer, text="Packets sent: UTC time, then the record (newest at the bottom)", padding=6)
        packets.pack(fill="both", expand=True)
        self.lines = tk.Text(packets, height=14, font=mono, wrap="none", state="disabled",
                             background="#0d1117", foreground="#e6edf3", relief="flat")
        scroll = ttk.Scrollbar(packets, command=self.lines.yview)
        self.lines.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.lines.pack(side="left", fill="both", expand=True)
        self.lines.tag_configure("error", foreground="#ff7b72")

        ttk.Label(outer, text="Fields: " + FIELDS, foreground=GREY, wraplength=730, justify="left"
                  ).pack(fill="x", pady=(8, 0))
        ttk.Label(outer, foreground=GREY,
                  text="Record format from AMDAS %s. Each record ends with CR LF." % AMDAS_VERSION
                  ).pack(fill="x")
        self.root.after(100, self.poll)

    # ---- start / stop

    def start(self):
        try:
            minutes = float(self.minutes.get())
            if not MIN_MINUTES <= minutes <= MAX_MINUTES:
                raise ValueError("the flight length must be %d to %d minutes" % (MIN_MINUTES, MAX_MINUTES))
            port = self.port.get().strip()
            if not port.isdigit():
                raise ValueError("the port must be a number, 1 to 65535")
            self.sender = Sender(self.host.get(), int(port), minutes, self.seven.get(),
                                 on_sent=lambda *event: self.events.put(event))
        except ValueError as exc:
            messagebox.showerror(TITLE, "Cannot start: %s." % exc, parent=self.root)
            return
        self.sender.start()
        for widget in (self.host_entry, self.port_entry, self.minutes_box, self.seven_box, self.start_button):
            widget.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.state.configure(text="Sending to %s:%d, first packet at the next second" % self.sender.address,
                             fg=GREEN)

    def stop(self):
        if self.sender:
            self.sender.stop()
            self.update_counts()
            self.sender = None
        for widget in (self.host_entry, self.port_entry, self.minutes_box, self.seven_box, self.start_button):
            widget.configure(state="normal")
        self.stop_button.configure(state="disabled")
        self.state.configure(text="Stopped", fg=GREY)
        self.flight.configure(text="")

    def close(self):
        self.stop()
        self.root.destroy()

    # ---- showing what was sent

    def update_counts(self):
        s = self.sender
        self.counts.configure(text="Sent %d · errors %d" % (s.sent, s.errors))

    def poll(self):
        try:
            while True:
                payload, point, error, utc_second = self.events.get_nowait()
                if self.sender is None:
                    continue
                self.add_line(payload, error, utc_second)
                self.update_counts()
                self.flight.configure(text="%s · %s ft above the field · GPS %s · %s of %s" % (
                    point.phase, format(round(point.altitude_ft), ","), FIX_NAMES[point.gps_fix],
                    mmss(point.elapsed), mmss(self.sender.flight.total)))
                if error:
                    self.state.configure(text="Send failed: %s" % error, fg=RED)
                else:
                    self.state.configure(text="Sending to %s:%d" % self.sender.address, fg=GREEN)
        except queue.Empty:
            pass
        self.root.after(100, self.poll)

    def add_line(self, payload, error, utc_second):
        text = "%s  %s" % (time.strftime("%H:%M:%S", time.gmtime(utc_second)), payload.decode("ascii").rstrip("\r\n"))
        if error:
            text += "   NOT SENT: " + error
        at_bottom = self.lines.yview()[1] >= 0.999
        self.lines.configure(state="normal")
        self.lines.insert("end", text + "\n", ("error",) if error else ())
        extra = int(self.lines.index("end-1c").split(".")[0]) - 1 - KEEP_LINES
        if extra > 0:
            self.lines.delete("1.0", "%d.0" % (extra + 1))
        self.lines.configure(state="disabled")
        if at_bottom:
            self.lines.see("end")


def self_test():
    """Send three records to a socket on this computer and check them; build the window. 0 = all good."""
    from sender import make_payload
    problems = []
    receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    receiver.bind(("127.0.0.1", 0))
    receiver.settimeout(5)
    for seven in (True, False):
        sender = Sender("127.0.0.1", receiver.getsockname()[1], 20, seven, seed=1)
        for second in (1795319332, 1795319333, 1795319334):
            payload, point, error = sender.send_once(second)
            got, source = receiver.recvfrom(1024)
            fields = got.decode("ascii").rstrip("\r\n").split(",")
            if error or got != payload or not got.endswith(b"\r\n") or len(fields) != (7 if seven else 6) \
                    or got != make_payload(point, second, seven):
                problems.append("record %r" % got)
        sender.stop()
    receiver.close()
    try:
        root = tk.Tk()
        Window(root)
        root.update()
        root.destroy()
    except Exception as exc:
        problems.append("window: %s" % exc)
    return problems


def main(argv=None):
    parser = argparse.ArgumentParser(description=TITLE)
    parser.add_argument("--self-test", metavar="RESULT_FILE", nargs="?", const="",
                        help="check sending and the window, exit 0 if all good; optionally write the result")
    args = parser.parse_args(argv)
    if args.self_test is not None:
        problems = self_test()
        result = "OK" if not problems else "FAIL: " + "; ".join(problems)
        if args.self_test:
            with open(args.self_test, "w") as out:
                out.write(result + "\n")
        elif sys.stdout:
            print(result)
        return 0 if not problems else 1
    root = tk.Tk()
    Window(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
