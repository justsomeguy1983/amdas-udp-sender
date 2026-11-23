"""AMDAS UDP test sender: a small window that sends the AMDAS UDP transmission once a second from a
simulated flight, so a receiver can be developed without the real unit.

  python amdas_udp_sender.py               open the window
  python amdas_udp_sender.py --self-test   check sending and the window work, exit 0 (good) or 1
"""

import argparse
import queue
import socket
import sys
import tkinter as tk
from tkinter import font as tkfont
from tkinter import messagebox, ttk

from amdas import AMDAS_VERSION
from flight import MAX_MINUTES, MIN_MINUTES
from sender import Sender

VERSION = "1.0.1"
TITLE = "AMDAS UDP test sender"
KEEP_LINES = 300
GREEN, RED, GREY = "#1a7f37", "#cf222e", "#57606a"
GPS_FIX = {0: "no fix", 1: "2D", 2: "3D", 3: "SBAS", 4: "differential", 5: "PPP", 6: "RTK float",
           7: "RTK fixed"}
# (field name, what the sent text means: a unit, or a function of the text)
FIELDS = (
    ("sys_time", lambda text: "= %02d:%02d:%02d UTC, seconds since midnight" % (
        int(text) // 3600, int(text) // 60 % 60, int(text) % 60)),
    ("pressure", "psia"),
    ("pressure altitude", "ft"),
    ("GPS altitude", "m above sea level"),
    ("temperature", "°C"),
    ("humidity", "%RH"),
    ("gps_fix", lambda text: "= %s" % GPS_FIX.get(int(text), "?")),
)


class Window:
    def __init__(self, root):
        self.root = root
        self.sender = None
        self.events = queue.Queue()
        root.title("%s %s" % (TITLE, VERSION))
        root.minsize(900, 580)
        root.protocol("WM_DELETE_WINDOW", self.close)
        mono = tkfont.nametofont("TkFixedFont").copy()
        mono.configure(size=10)
        mono_bold = mono.copy()
        mono_bold.configure(weight="bold")

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
        ttk.Label(settings, foreground=GREY, wraplength=840, justify="left",
                  text="127.0.0.1 is this computer: the packets then come from 127.0.0.1, not from the "
                       "real unit's address, so the receiver must accept that source while testing."
                  ).grid(row=2, column=0, columnspan=7, sticky="w", pady=(6, 0))

        buttons = ttk.Frame(outer, padding=(0, 10, 0, 8))
        buttons.pack(fill="x")
        self.start_button = ttk.Button(buttons, text="Start", command=self.start, width=12)
        self.start_button.pack(side="left")
        self.stop_button = ttk.Button(buttons, text="Stop", command=self.stop, width=12, state="disabled")
        self.stop_button.pack(side="left", padx=(8, 0))
        self.state = tk.Label(buttons, text="Stopped", fg=GREY, font=("TkDefaultFont", 11, "bold"))
        self.state.pack(side="left", padx=(16, 0))
        self.counts = ttk.Label(buttons, text="")
        self.counts.pack(side="right")

        # What goes out: the packets, exactly, and the last one taken apart
        sent = ttk.Frame(outer)
        sent.pack(fill="both", expand=True)
        packets = ttk.LabelFrame(sent, text="SENT: each packet exactly as it goes out (newest at the bottom)",
                                 padding=6)
        packets.pack(side="left", fill="both", expand=True)
        text_area = ttk.Frame(packets)
        text_area.pack(fill="both", expand=True)
        self.lines = tk.Text(text_area, height=12, width=52, font=mono, wrap="none", state="disabled",
                             background="#0d1117", foreground="#e6edf3", relief="flat")
        scroll = ttk.Scrollbar(text_area, command=self.lines.yview)
        self.lines.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.lines.pack(side="left", fill="both", expand=True)
        ttk.Label(packets, foreground=GREY, text="Every packet also ends with CR LF (not shown). Nothing "
                  "else is sent.").pack(anchor="w", pady=(4, 0))

        table = ttk.LabelFrame(sent, text="SENT: the last packet, field by field", padding=8)
        table.pack(side="left", fill="y", padx=(10, 0))
        ttk.Label(table, text="Field", foreground=GREY).grid(row=0, column=0, sticky="w")
        ttk.Label(table, text="Sent", foreground=GREY).grid(row=0, column=1, sticky="w", padx=10)
        ttk.Label(table, text="Means", foreground=GREY).grid(row=0, column=2, sticky="w")
        self.field_values = []
        for row, (name, _) in enumerate(FIELDS, start=1):
            ttk.Label(table, text="%d  %s" % (row, name)).grid(row=row, column=0, sticky="w", pady=2)
            value = ttk.Label(table, text="", font=mono_bold)
            value.grid(row=row, column=1, sticky="w", padx=10)
            means = ttk.Label(table, text="", foreground=GREY)
            means.grid(row=row, column=2, sticky="w")
            self.field_values.append((value, means))

        ttk.Label(outer, foreground=GREY, text="Record format from AMDAS %s." % AMDAS_VERSION
                  ).pack(anchor="w", pady=(8, 0))
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

    def close(self):
        self.stop()
        self.root.destroy()

    # ---- showing what was sent

    def update_counts(self):
        s = self.sender
        self.counts.configure(text="Sent %d · failed %d" % (s.sent, s.errors))

    def poll(self):
        try:
            while True:
                payload, point, error, utc_second = self.events.get_nowait()
                if self.sender is None:
                    continue
                self.update_counts()
                if error:
                    self.state.configure(text="Send failed: %s" % error, fg=RED)
                    continue
                self.state.configure(text="Sending to %s:%d" % self.sender.address, fg=GREEN)
                self.add_line(payload)
                self.show_fields(payload)
        except queue.Empty:
            pass
        self.root.after(100, self.poll)

    def add_line(self, payload):
        at_bottom = self.lines.yview()[1] >= 0.999
        self.lines.configure(state="normal")
        self.lines.insert("end", payload.decode("ascii").rstrip("\r\n") + "\n")
        extra = int(self.lines.index("end-1c").split(".")[0]) - 1 - KEEP_LINES
        if extra > 0:
            self.lines.delete("1.0", "%d.0" % (extra + 1))
        self.lines.configure(state="disabled")
        if at_bottom:
            self.lines.see("end")

    def show_fields(self, payload):
        values = payload.decode("ascii").rstrip("\r\n").split(",")
        for i, ((name, means), (value_label, means_label)) in enumerate(zip(FIELDS, self.field_values)):
            if i >= len(values):
                value_label.configure(text="—")
                means_label.configure(text="not sent (6-field record)")
                continue
            text = values[i]
            value_label.configure(text=text)
            means_label.configure(text=means(text) if callable(means) else means)


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
        window = Window(root)
        for record in (b"51893,+14.70098,-0009.48,+0000.00,+21.01,+054.73,0\r\n",
                       b"51893,+14.70098,-0009.48,+0000.00,+21.01,+054.73\r\n"):
            window.add_line(record)
            window.show_fields(record)
        if window.field_values[0][1].cget("text") != "= 14:24:53 UTC, seconds since midnight":
            problems.append("window: sys_time shown as %r" % window.field_values[0][1].cget("text"))
        if window.field_values[6][0].cget("text") != "—":
            problems.append("window: gps_fix shown for a 6-field record")
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
