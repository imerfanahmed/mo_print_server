import customtkinter as ctk
import asyncio
import threading
import json
import base64
import socket
import logging
import pysher
import time
import sys
import os
import winreg

try:
    import win32print
except ImportError:
    win32print = None

# --- Logic for Different Printer Types ---
def print_to_windows_spooler(printer_name, raw_data):
    if not win32print:
        print(f"Would print to Windows spooler: {printer_name}")
        return
    hPrinter = win32print.OpenPrinter(printer_name)
    try:
        hJob = win32print.StartDocPrinter(hPrinter, 1, ("RMS Print", None, "RAW"))
        win32print.StartPagePrinter(hPrinter)
        win32print.WritePrinter(hPrinter, raw_data)
        win32print.EndPagePrinter(hPrinter)
        win32print.EndDocPrinter(hPrinter)
    finally:
        win32print.ClosePrinter(hPrinter)

def print_to_network_ip(ip_address, port, raw_data):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(5)
        s.connect((ip_address, int(port)))
        s.sendall(raw_data)

def set_autostart(enable=True):
    key = winreg.HKEY_CURRENT_USER
    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    app_name = "RMS Print Server"
    
    if getattr(sys, 'frozen', False):
        exe_path = sys.executable
    else:
        exe_path = f'"{sys.executable}" "{os.path.abspath(__file__)}"'

    try:
        registry_key = winreg.OpenKey(key, key_path, 0, winreg.KEY_SET_VALUE)
        if enable:
            winreg.SetValueEx(registry_key, app_name, 0, winreg.REG_SZ, exe_path)
        else:
            winreg.DeleteValue(registry_key, app_name)
        winreg.CloseKey(registry_key)
        return True
    except Exception as e:
        print(f"Autostart Error: {e}")
        return False

def check_autostart():
    key = winreg.HKEY_CURRENT_USER
    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    app_name = "RMS Print Server"
    try:
        registry_key = winreg.OpenKey(key, key_path, 0, winreg.KEY_READ)
        value, _ = winreg.QueryValueEx(registry_key, app_name)
        winreg.CloseKey(registry_key)
        return True
    except WindowsError:
        return False

# --- UI Application ---
class PrintServerUI(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("RMS Cloud Print Gateway")
        self.geometry("700x700")
        
        # Configuration Variables
        self.printers = []
        self.printer_mapping = {}
        if win32print:
            for p in win32print.EnumPrinters(win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS):
                printer_name = p[2]
                display_name = printer_name
                try:
                    hprinter = win32print.OpenPrinter(printer_name)
                    info = win32print.GetPrinter(hprinter, 2)
                    share_name = info.get('pShareName', '')
                    win32print.ClosePrinter(hprinter)
                    if share_name:
                        display_name = f"{printer_name} ({share_name})"
                except Exception:
                    pass
                self.printers.append(display_name)
                self.printer_mapping[display_name] = printer_name
        else:
            self.printers = ["Mock Printer 1", "Mock Printer 2"]
            self.printer_mapping = {p: p for p in self.printers}
            
        self.server_config = {}
        try:
            import os
            if os.path.exists("server_config.json"):
                with open("server_config.json", "r") as f:
                    self.server_config = json.load(f)
        except Exception:
            pass

        saved_printer = self.server_config.get("selected_printer", "")
        default_printer = ""
        for disp, actual in self.printer_mapping.items():
            if actual == saved_printer or disp == saved_printer:
                default_printer = disp
                break
        
        if not default_printer and self.printers:
            default_printer = self.printers[0]

        self.selected_printer = ctk.StringVar(value=default_printer)
        self.connection_type = ctk.StringVar(value=self.server_config.get("connection_type", "USB/System"))
        
        # Pusher Config
        self.pusher_connected = False
        self.pusher_client = None
        self.pusher_config = {}
        try:
            import os
            if os.path.exists("pusher_config.json"):
                with open("pusher_config.json", "r") as f:
                    self.pusher_config = json.load(f)
        except Exception:
            pass

        self.setup_ui()
        
        # Connect to Pusher if config exists
        if self.pusher_config.get("key"):
            self.connect_to_pusher()

    def setup_ui(self):
        # Settings Header
        ctk.CTkLabel(self, text="Printer Settings", font=("Arial", 16, "bold")).pack(pady=(10, 5))

        # Connection Type Toggle
        self.type_menu = ctk.CTkSegmentedButton(self, values=["USB/System", "Network IP"], 
                                                variable=self.connection_type, command=self.toggle_inputs)
        self.type_menu.pack(pady=5)

        # Printer Config Frame
        self.input_frame = ctk.CTkFrame(self)
        self.input_frame.pack(pady=5, padx=20, fill="x")
        self.refresh_inputs()
        
        self.save_printer_btn = ctk.CTkButton(self, text="Save Printer Settings", command=self.save_printer_config)
        self.save_printer_btn.pack(pady=5)
        
        # Pusher Settings Header
        ctk.CTkLabel(self, text="Cloud Connection (Pusher)", font=("Arial", 16, "bold")).pack(pady=(15, 5))
        
        # Pusher Config Frame
        self.pusher_frame = ctk.CTkFrame(self)
        self.pusher_frame.pack(pady=5, padx=20, fill="x")
        
        ctk.CTkLabel(self.pusher_frame, text="Paste Pusher Credentials:").pack(anchor="w", padx=5)
        self.pusher_config_text = ctk.CTkTextbox(self.pusher_frame, height=120)
        self.pusher_config_text.pack(pady=5, padx=5, fill="x")
        
        default_text = (
            f'app_id = "{self.pusher_config.get("app_id", "")}"\n'
            f'key = "{self.pusher_config.get("key", "")}"\n'
            f'secret = "{self.pusher_config.get("secret", "")}"\n'
            f'cluster = "{self.pusher_config.get("cluster", "eu")}"\n'
            f'channel = "{self.pusher_config.get("channel", "print-channel")}"'
        )
        self.pusher_config_text.insert("0.0", default_text)

        self.connect_btn = ctk.CTkButton(self.pusher_frame, text="Connect", command=self.save_and_connect_pusher)
        self.connect_btn.pack(pady=5)

        self.pusher_status_lbl = ctk.CTkLabel(self, text="Status: Disconnected", text_color="red")
        self.pusher_status_lbl.pack(pady=5)
        
        # Autostart Option
        self.autostart_var = ctk.BooleanVar(value=check_autostart())
        self.autostart_chk = ctk.CTkCheckBox(self, text="Start Automatically on Windows Boot", variable=self.autostart_var, command=self.toggle_autostart)
        self.autostart_chk.pack(pady=5)

        # Log Window
        ctk.CTkLabel(self, text="Activity Log", font=("Arial", 14, "bold")).pack(pady=(10, 0))
        self.log_text = ctk.CTkTextbox(self, height=150)
        self.log_text.pack(pady=5, padx=20, fill="both", expand=True)
        
        self.test_btn = ctk.CTkButton(self, text="Send Test Print", fg_color="green", command=self.handle_test_print)
        self.test_btn.pack(pady=10)
    def get_actual_printer_name(self, display_name):
        return self.printer_mapping.get(display_name, display_name)

    def handle_test_print(self):
        # ESC/POS Commands
        ESC = b'\x1b'
        GS = b'\x1d'
        
        # Formatting
        INIT = ESC + b'@'
        ALIGN_CENTER = ESC + b'a' + b'\x01'
        ALIGN_LEFT = ESC + b'a' + b'\x00'
        BOLD_ON = ESC + b'E' + b'\x01'
        BOLD_OFF = ESC + b'E' + b'\x00'
        FONT_B = ESC + b'M' + b'\x01'
        CUT = GS + b'V' + b'\x42' + b'\x00'

        target_name = self.selected_printer.get() if self.connection_type.get() == "USB/System" else f"{self.ip_entry.get()}:{self.port_entry.get()}"

        test_data = (
            INIT +
            ALIGN_CENTER + BOLD_ON + b"RMS Print Server\n" + BOLD_OFF +
            b"Test Page\n\n" +
            ALIGN_LEFT +
            b"--------------------------------\n" +
            b"Connection: " + self.connection_type.get().encode('utf-8') + b"\n" +
            b"Target: " + target_name.encode('utf-8') + b"\n" +
            b"Time: " + self.get_time().encode('utf-8') + b"\n" +
            b"--------------------------------\n" +
            ALIGN_CENTER + b"If you can read this,\nprinter setup is successful!\n" +
            b"\n" + FONT_B + b"(End of Test)\n" +
            b"\n\n\n\n" + 
            CUT
        )
        
        try:
            if self.connection_type.get() == "USB/System":
                actual_printer = self.get_actual_printer_name(self.selected_printer.get())
                print_to_windows_spooler(actual_printer, test_data)
            else:
                print_to_network_ip(self.ip_entry.get() if hasattr(self, 'ip_entry') and self.ip_entry else "", self.port_entry.get() if hasattr(self, 'port_entry') and self.port_entry else "9100", test_data)
            self.log(f"Extensive test print sent to {self.connection_type.get()}")
        except Exception as e:
            self.log(f"Error: {e}")

    def toggle_autostart(self):
        enabled = self.autostart_var.get()
        success = set_autostart(enabled)
        if success:
            state = "enabled" if enabled else "disabled"
            self.log(f"Auto-startup on boot is now {state}.")
        else:
            self.autostart_var.set(not enabled) # Revert UI
            self.log("Failed to modify Windows Registry for auto-start.")

    def toggle_inputs(self, value):
        for widget in self.input_frame.winfo_children():
            widget.destroy()
        self.refresh_inputs()

    def refresh_inputs(self):
        if self.connection_type.get() == "USB/System":
            ctk.CTkLabel(self.input_frame, text="Select Installed Printer:").pack(side="left", padx=5)
            self.p_menu = ctk.CTkOptionMenu(self.input_frame, values=self.printers, variable=self.selected_printer)
            self.p_menu.pack(side="left", padx=5)
            self.ip_entry = None
            self.port_entry = None
        else:
            ctk.CTkLabel(self.input_frame, text="IP:").pack(side="left", padx=5)
            self.ip_entry = ctk.CTkEntry(self.input_frame, placeholder_text="192.168.1.100")
            self.ip_entry.pack(side="left", padx=5)
            if self.server_config.get("network_ip"):
                self.ip_entry.insert(0, self.server_config.get("network_ip"))

            ctk.CTkLabel(self.input_frame, text="Port:").pack(side="left", padx=5)
            self.port_entry = ctk.CTkEntry(self.input_frame, placeholder_text="9100", width=70)
            self.port_entry.insert(0, self.server_config.get("network_port", "9100"))
            self.port_entry.pack(side="left", padx=5)

    def log(self, message):
        self.log_text.insert("end", f"[{self.get_time()}] {message}\n")
        self.log_text.see("end")

    def get_time(self):
        from datetime import datetime
        return datetime.now().strftime("%H:%M:%S")

    def save_printer_config(self):
        config = {
            "connection_type": self.connection_type.get(),
            "selected_printer": self.get_actual_printer_name(self.selected_printer.get())
        }
        if self.connection_type.get() == "Network IP":
            config["network_ip"] = self.ip_entry.get() if self.ip_entry else ""
            config["network_port"] = self.port_entry.get() if self.port_entry else "9100"
            
        self.server_config = config
        try:
            with open("server_config.json", "w") as f:
                json.dump(config, f)
            self.log("Printer settings saved.")
        except Exception as e:
            self.log(f"Error saving printer settings: {e}")

    def save_and_connect_pusher(self):
        text = self.pusher_config_text.get("0.0", "end").strip()
        config = {}
        for line in text.split('\n'):
            line = line.strip()
            if '=' in line:
                k, v = line.split('=', 1)
                k = k.strip()
                v = v.strip().strip('",\'')
                config[k] = v
        
        if "channel" not in config:
            config["channel"] = "print-channel"
            
        self.pusher_config = config
        try:
            with open("pusher_config.json", "w") as f:
                json.dump(config, f)
            self.log("Configuration saved.")
        except Exception as e:
            self.log(f"Error saving config: {e}")

        self.connect_to_pusher()

    def connect_to_pusher(self):
        # Disconnect existing connection if any
        if self.pusher_client:
            try:
                self.pusher_client.disconnect()
            except:
                pass
            self.pusher_client = None

        key = self.pusher_config.get("key")
        cluster = self.pusher_config.get("cluster", "eu")
        
        if not key:
            self.log("Pusher key missing. Cannot connect.")
            return

        self.log(f"Initializing Pusher client (Cluster: {cluster})...")
        self.pusher_client = pysher.Pusher(key, cluster=cluster)

        # Attach connection handler
        self.pusher_client.connection.bind('pusher:connection_established', self.on_pusher_connected)
        self.pusher_client.connection.bind('pusher:connection_failed', self.on_pusher_failed)
        self.pusher_client.connection.bind('pusher:error', self.on_pusher_error)

        self.pusher_client.connect()

    def on_pusher_connected(self, data):
        self.pusher_connected = True
        self.pusher_status_lbl.configure(text="Status: Connected", text_color="green")
        self.log("Connected to Pusher successfully.")
        
        channel_name = self.pusher_config.get('channel', 'print-channel')
        channel = self.pusher_client.subscribe(channel_name)
        channel.bind('App\\Events\\PrintJobReceived', self.handle_print_event) # Laravel style default event
        channel.bind('App\\Events\\PrintJobDispatched', self.handle_print_event) 
        channel.bind('print-event', self.handle_print_event) # Generic event fallback
        channel.bind('print', self.handle_print_event) # Our actual broadcast name
        self.log(f"Subscribed to channel: {channel_name}")

    def on_pusher_failed(self, data):
        self.pusher_connected = False
        self.pusher_status_lbl.configure(text="Status: Connection Failed", text_color="red")
        self.log(f"Pusher connection failed: {data}")

    def on_pusher_error(self, data):
        self.log(f"Pusher Error: {data}")

    def handle_print_event(self, *args, **kwargs):
        # Pysher passes the event payload as the first argument, typically as a JSON string
        try:
            if not args:
                return

            raw_data = args[0]
            if isinstance(raw_data, str):
                payload = json.loads(raw_data)
            else:
                payload = raw_data

            content_to_print = None
            
            if isinstance(payload, dict):
                if 'content' in payload:
                    content_to_print = payload['content']
                elif 'data' in payload:
                    content_to_print = payload['data']
                else:
                    content_to_print = payload.get('message', '')

            if content_to_print:
                try:
                    decoded = base64.b64decode(content_to_print)
                    content_to_print = decoded
                    
                    readable_text = ''.join(chr(b) if 32 <= b <= 126 or b == 10 else '' for b in decoded)
                    self.log(f"Received print job via Pusher ({len(decoded)} bytes)")
                    self.log(f"--- Decoded Data Preview ---\n{readable_text.strip()}\n----------------------------")
                except:
                    if isinstance(content_to_print, str):
                        content_to_print = content_to_print.encode('utf-8')
                    self.log(f"Received print job via Pusher ({len(content_to_print)} bytes)")
                
                # Determine connection and printer from payload if available
                conn_type = self.connection_type.get()
                printer_target = self.selected_printer.get()
                ip_target = self.ip_entry.get() if hasattr(self, 'ip_entry') and self.ip_entry else ""
                port_target = self.port_entry.get() if hasattr(self, 'port_entry') and self.port_entry else "9100"

                if isinstance(payload, dict):
                    if payload.get("connectivity") == "network":
                        conn_type = "Network IP"
                        printer_ip = payload.get("printer", "")
                        if ":" in printer_ip:
                            ip_target, port_target = printer_ip.split(":", 1)
                        else:
                            ip_target = printer_ip
                    elif payload.get("connectivity") == "usb":
                        conn_type = "USB/System"
                        printer_target = payload.get("printer", printer_target)

                if conn_type == "USB/System":
                    actual_printer = self.get_actual_printer_name(printer_target)
                    print_to_windows_spooler(actual_printer, content_to_print)
                else:
                    if not ip_target:
                        self.log("Error: Network IP target is empty from both UI and Payload")
                    else:
                        print_to_network_ip(ip_target, port_target, content_to_print)
            else:
                self.log("Received event but found no printable content")

        except Exception as e:
            self.log(f"Error processing Pusher event: {e}")

    def handle_test_print(self):
        # ESC/POS Commands
        ESC = b'\x1b'
        GS = b'\x1d'
        
        # Formatting
        INIT = ESC + b'@'
        ALIGN_CENTER = ESC + b'a' + b'\x01'
        ALIGN_LEFT = ESC + b'a' + b'\x00'
        BOLD_ON = ESC + b'E' + b'\x01'
        BOLD_OFF = ESC + b'E' + b'\x00'
        FONT_B = ESC + b'M' + b'\x01'
        CUT = GS + b'V' + b'\x42' + b'\x00'

        target_name = self.selected_printer.get() if self.connection_type.get() == "USB/System" else f"{self.ip_entry.get()}:{self.port_entry.get()}"

        test_data = (
            INIT +
            ALIGN_CENTER + BOLD_ON + b"RMS Print Server\n" + BOLD_OFF +
            b"Test Page\n\n" +
            ALIGN_LEFT +
            b"--------------------------------\n" +
            b"Connection: " + self.connection_type.get().encode('utf-8') + b"\n" +
            b"Target: " + target_name.encode('utf-8') + b"\n" +
            b"Time: " + self.get_time().encode('utf-8') + b"\n" +
            b"--------------------------------\n" +
            ALIGN_CENTER + b"If you can read this,\nprinter setup is successful!\n" +
            b"\n" + FONT_B + b"(End of Test)\n" +
            b"\n\n\n\n" + 
            CUT
        )
        
        try:
            if self.connection_type.get() == "USB/System":
                print_to_windows_spooler(self.selected_printer.get(), test_data)
            else:
                print_to_network_ip(self.ip_entry.get(), self.port_entry.get(), test_data)
            self.log(f"Extensive test print sent to {self.connection_type.get()}")
        except Exception as e:
            self.log(f"Error: {e}")

if __name__ == "__main__":
    app = PrintServerUI()
    app.mainloop()