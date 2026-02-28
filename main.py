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
import pystray
from PIL import Image, ImageDraw

try:
    import win32print
except ImportError:
    win32print = None

def get_base_path():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

def get_config_path(filename):
    # Determine the AppData directory to store configuration safely 
    # (since writing to Program Files requires admin rights)
    appdata_dir = os.path.join(os.environ.get('LOCALAPPDATA', os.path.expanduser('~')), 'MagicOfficeRMS')
    os.makedirs(appdata_dir, exist_ok=True)
    return os.path.join(appdata_dir, filename)

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
        s.settimeout(3) # Reduce timeout so config push fails fast if TCP doesn't work
        s.connect((ip_address, int(port)))
        s.sendall(raw_data)

def push_config_udp(ip_address, raw_data):
    # Many printers have a config UDP port (e.g. 40000, or just broadcast 9100)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s.sendto(raw_data, (ip_address, 40000))
        s.sendto(raw_data, (ip_address, 9100))

def set_autostart(enable=True):
    key = winreg.HKEY_CURRENT_USER
    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    app_name = "RMS Print Server"
    
    if getattr(sys, 'frozen', False):
        exe_path = f'"{sys.executable}" --hide'
    else:
        exe_path = f'"{sys.executable}" "{os.path.abspath(__file__)}" --hide'

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
        self.title("Magic Office RMS Cloud Print Gateway")
        self.geometry("700x700")

        # System Tray Hook
        self.protocol('WM_DELETE_WINDOW', self.withdraw_window)
        self.icon_image = self.create_image()
        self.tray_icon = None
        
        # Discover Printers
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
            
        default_printer = self.printers[0] if self.printers else ""
        self.selected_printer = ctk.StringVar(value=default_printer)
        self.connection_type = ctk.StringVar(value="USB/System")
        
        # Pusher Config
        self.pusher_connected = False
        self.pusher_client = None
        self.pusher_config = {}
        try:
            config_path = get_config_path("pusher_config.json")
            if os.path.exists(config_path):
                with open(config_path, "r") as f:
                    self.pusher_config = json.load(f)
        except Exception:
            pass

        self.setup_ui()
        
        # Connect to Pusher if config exists
        if self.pusher_config.get("key"):
            self.connect_to_pusher()

    def setup_ui(self):
        # Setup Tabs
        self.tabview = ctk.CTkTabview(self, height=350)
        self.tabview.pack(padx=20, pady=10, fill="x")

        self.tab_config = self.tabview.add("Print Server Config")
        self.tab_test = self.tabview.add("Test Print")
        # self.tab_network = self.tabview.add("Network Config")

        # --- Print Server Config Tab ---
        ctk.CTkLabel(self.tab_config, text="Cloud Connection (Pusher)", font=("Arial", 16, "bold")).pack(pady=(10, 5))
        
        self.pusher_frame = ctk.CTkFrame(self.tab_config)
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

        self.connect_btn = ctk.CTkButton(self.pusher_frame, text="Save & Connect", command=self.save_and_connect_pusher)
        self.connect_btn.pack(pady=5)

        self.pusher_status_lbl = ctk.CTkLabel(self.tab_config, text="Status: Disconnected", text_color="red")
        self.pusher_status_lbl.pack(pady=5)
        
        self.autostart_var = ctk.BooleanVar(value=check_autostart())
        self.autostart_chk = ctk.CTkCheckBox(self.tab_config, text="Start Automatically on Windows Boot", variable=self.autostart_var, command=self.toggle_autostart)
        self.autostart_chk.pack(pady=10)

        # --- Test Print Tab ---
        ctk.CTkLabel(self.tab_test, text="Test Printer Connectivity", font=("Arial", 16, "bold")).pack(pady=(10, 5))

        self.type_menu = ctk.CTkSegmentedButton(self.tab_test, values=["USB/System", "Network IP"], 
                                                variable=self.connection_type, command=self.toggle_inputs)
        self.type_menu.pack(pady=10)

        self.input_frame = ctk.CTkFrame(self.tab_test)
        self.input_frame.pack(pady=10, padx=20, fill="x")
        
        self.test_btn = ctk.CTkButton(self.tab_test, text="Send Test Print", fg_color="green", command=self.handle_test_print)
        self.test_btn.pack(pady=15)
        
        self.refresh_inputs()

        # # --- Network Config Tab ---
        # ctk.CTkLabel(self.tab_network, text="Push Network Configuration", font=("Arial", 16, "bold")).pack(pady=(10, 5))
        # ctk.CTkLabel(self.tab_network, text="Warning: This works on most generic ESC/POS network printers.\nThe printer will restart automatically after sending.", text_color="orange").pack(pady=5)
        # 
        # net_frame = ctk.CTkFrame(self.tab_network)
        # net_frame.pack(pady=10, padx=20, fill="x")

        # # Current Connection
        # ctk.CTkLabel(net_frame, text="Current IP (to connect to):").grid(row=0, column=0, padx=5, pady=5, sticky="e")
        # self.net_current_ip = ctk.CTkEntry(net_frame, placeholder_text="192.168.1.100")
        # self.net_current_ip.grid(row=0, column=1, padx=5, pady=5, sticky="w")

        # # New Configuration
        # ctk.CTkLabel(net_frame, text="New IP Address:").grid(row=1, column=0, padx=5, pady=5, sticky="e")
        # self.net_new_ip = ctk.CTkEntry(net_frame, placeholder_text="192.168.1.200")
        # self.net_new_ip.grid(row=1, column=1, padx=5, pady=5, sticky="w")

        # ctk.CTkLabel(net_frame, text="Subnet Mask:").grid(row=2, column=0, padx=5, pady=5, sticky="e")
        # self.net_mask = ctk.CTkEntry(net_frame, placeholder_text="255.255.255.0")
        # self.net_mask.insert(0, "255.255.255.0")
        # self.net_mask.grid(row=2, column=1, padx=5, pady=5, sticky="w")

        # ctk.CTkLabel(net_frame, text="Gateway:").grid(row=3, column=0, padx=5, pady=5, sticky="e")
        # self.net_gateway = ctk.CTkEntry(net_frame, placeholder_text="192.168.1.1")
        # self.net_gateway.insert(0, "192.168.1.1")
        # self.net_gateway.grid(row=3, column=1, padx=5, pady=5, sticky="w")

        # self.net_push_btn = ctk.CTkButton(self.tab_network, text="Push Configuration to Printer", fg_color="red", command=self.handle_network_config_push)
        # self.net_push_btn.pack(pady=15)

        # --- Shared Log Window ---
        ctk.CTkLabel(self, text="Activity Log", font=("Arial", 14, "bold")).pack(pady=(10, 0))
        self.log_text = ctk.CTkTextbox(self)
        self.log_text.pack(pady=(5, 20), padx=20, fill="both", expand=True)

    def get_actual_printer_name(self, display_name):
        return self.printer_mapping.get(display_name, display_name)

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

            ctk.CTkLabel(self.input_frame, text="Port:").pack(side="left", padx=5)
            self.port_entry = ctk.CTkEntry(self.input_frame, placeholder_text="9100", width=70)
            self.port_entry.insert(0, "9100")
            self.port_entry.pack(side="left", padx=5)

    def log(self, message):
        self.log_text.insert("end", f"[{self.get_time()}] {message}\n")
        self.log_text.see("end")

    def get_time(self):
        from datetime import datetime
        return datetime.now().strftime("%H:%M:%S")

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
            with open(get_config_path("pusher_config.json"), "w") as f:
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

            # --- LOG THE FULL PAYLOAD EVENT ---
            self.log(f"--- INCOMING EVENT FROM PUSHER ---")
            
            # Use json.dumps to pretty print the incoming mapping and identifiers
            if isinstance(payload, dict):
                # We filter out the raw content string to keep logs readable, but show everything else!
                log_payload = {k: v for k, v in payload.items() if k not in ['content', 'data', 'message']}
                if 'content' in payload or 'data' in payload:
                    log_payload['content'] = "[...base64 content truncated for log readability...]"
                self.log(json.dumps(log_payload, indent=2))
            else:
                self.log(str(payload))
            self.log("----------------------------------")

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
                
                # Determine connection and printer from payload
                if isinstance(payload, dict):
                    conn_type = payload.get("connectivity")
                    printer_target = payload.get("printer")

                    self.log(f"Routing to -> Connection: {conn_type}, Target: {printer_target}")

                    if conn_type in ("network", "ip") and printer_target:
                        if ":" in printer_target:
                            ip_target, port_target = printer_target.split(":", 1)
                        else:
                            ip_target = printer_target
                            port_target = "9100"
                        print_to_network_ip(ip_target, port_target, content_to_print)
                        self.log(f"Success: Print job sent to network printer: {printer_target}")
                    elif conn_type == "usb" and printer_target:
                        actual_printer = self.get_actual_printer_name(printer_target)
                        print_to_windows_spooler(actual_printer, content_to_print)
                        self.log(f"Success: Print job sent to USB/System printer: {actual_printer}")
                    else:
                        self.log(f"Error: Invalid payload format or missing destination. Connectivity: '{conn_type}', Printer: '{printer_target}'")
                else:
                    self.log("Error: Payload is not a dictionary. Cannot determine printer destination.")
            else:
                self.log("Error: Received event but found no printable content")

        except Exception as e:
            self.log(f"Error processing Pusher event: {e}")

    def handle_test_print(self):
        # ESC/POS Commands
        ESC = b'\x1b'
        GS = b'\x1d'
        
        # Formatting
        INIT = ESC + b'@'
        SET_80MM_WIDTH = GS + b'W' + b'\x80\x02'  # Set print area width: 576 dots (80mm standard)
        ALIGN_CENTER = ESC + b'a' + b'\x01'
        ALIGN_LEFT = ESC + b'a' + b'\x00'
        ALIGN_RIGHT = ESC + b'a' + b'\x02'
        BOLD_ON = ESC + b'E' + b'\x01'
        BOLD_OFF = ESC + b'E' + b'\x00'
        FONT_A = ESC + b'M' + b'\x00'
        FONT_B = ESC + b'M' + b'\x01'
        DOUBLE_HEIGHT = ESC + b'!' + b'\x10'
        DOUBLE_WIDTH = ESC + b'!' + b'\x20'
        DOUBLE_HW = ESC + b'!' + b'\x30'
        NORMAL_TEXT = ESC + b'!' + b'\x00'
        INVERT_ON = GS + b'B' + b'\x01'
        INVERT_OFF = GS + b'B' + b'\x00'
        CUT = GS + b'V' + b'\x42' + b'\x00'

        conn_type = self.connection_type.get()
        if conn_type == "USB/System":
            target_name = self.selected_printer.get()
        else:
            ip = self.ip_entry.get() if hasattr(self, 'ip_entry') and self.ip_entry else ""
            port = self.port_entry.get() if hasattr(self, 'port_entry') and self.port_entry else "9100"
            target_name = f"{ip}:{port}"

        # ASCII Character Map generator
        chars = "".join(chr(i) for i in range(33, 127))
        char_map = b""
        for i in range(0, len(chars), 32):
            char_map += chars[i:i+32].encode('ascii') + b"\n"

        # Generate a sample 200x200 pixel test logo image (A hollow square with an X)
        img_size = 200
        from PIL import Image, ImageDraw
        test_img = Image.new('1', (img_size, img_size), 1) # 1-bit monochrome, white background
        draw = ImageDraw.Draw(test_img)
        draw.rectangle([10, 10, img_size-10, img_size-10], outline=0, width=5)
        draw.line([10, 10, img_size-10, img_size-10], fill=0, width=5)
        draw.line([img_size-10, 10, 10, img_size-10], fill=0, width=5)
        
        # Convert PIL Image to ESC/POS Raster Bit Image format (GS v 0)
        img_bytes = test_img.tobytes()
        width_bytes = img_size // 8
        height_dots = img_size
        
        # GS v 0 p (Normal mode) xL xH yL yH [data...]
        IMG_CMD = GS + b'v0' + b'\x00' + \
                  bytes([width_bytes % 256, width_bytes // 256]) + \
                  bytes([height_dots % 256, height_dots // 256]) + \
                  img_bytes

        # Print the printer's internal self-test page
        # Command varies by manufacturer, but these cover 95% of Chinese clones
        # (Epson often uses GS ( A ) 
        SELF_TEST = GS + b'(' + b'A' + b'\x02' + b'\x00' + b'\x00' + b'\x02' # GS ( A standard self test
        SELF_TEST_NETWORK = GS + b'\x28' + b'\x45' + b'\x02' + b'\x00' + b'\x01' + b'\x49' # Network info self test

        test_data = (
            INIT + SET_80MM_WIDTH +
            ALIGN_CENTER + INVERT_ON + b"   MAGIC OFFICE RMS SELF-TEST   \n" + INVERT_OFF + b"\n" +
            ALIGN_LEFT + NORMAL_TEXT +
            b"Printer Diagnostics & Configuration\n" +
            b"--------------------------------\n" +
            b"Target: " + target_name.encode('utf-8') + b"\n" +
            b"Connection: " + conn_type.encode('utf-8') + b"\n" +
            b"Time: " + self.get_time().encode('utf-8') + b"\n" +
            b"--------------------------------\n\n" +
            ALIGN_CENTER + BOLD_ON + b"--- IMAGE TEST (200x200) ---\n" + BOLD_OFF +
            IMG_CMD + b"\n\n" +
            ALIGN_CENTER + BOLD_ON + b"--- TEXT FORMATTING ---\n" + BOLD_OFF + ALIGN_LEFT +
            NORMAL_TEXT + b"Normal Text\n" +
            FONT_B + b"Font B (Small Text)\n" + FONT_A +
            BOLD_ON + b"Bold Text\n" + BOLD_OFF +
            DOUBLE_HEIGHT + b"Double Height\n" + NORMAL_TEXT +
            DOUBLE_WIDTH + b"Double Width\n" + NORMAL_TEXT +
            DOUBLE_HW + b"Double Size\n" + NORMAL_TEXT +
            b"\n" + 
            ALIGN_CENTER + BOLD_ON + b"--- ALIGNMENT ---\n" + BOLD_OFF +
            ALIGN_LEFT + b"Left Aligned\n" +
            ALIGN_CENTER + b"Center Aligned\n" +
            ALIGN_RIGHT + b"Right Aligned\n" + ALIGN_LEFT + b"\n" +
            ALIGN_CENTER + BOLD_ON + b"--- CHARACTER SET ---\n" + BOLD_OFF + ALIGN_LEFT + FONT_B +
            char_map + NORMAL_TEXT + b"\n" +
            ALIGN_CENTER + BOLD_ON + b"--- BARCODE TEST ---\n" + BOLD_OFF +
            # CODE39 barcode
            GS + b'h' + chr(80).encode('ascii') + # Height 80
            GS + b'w' + chr(2).encode('ascii') + # Width 2
            GS + b'f' + chr(0).encode('ascii') + # Font for HRI
            GS + b'H' + chr(2).encode('ascii') + # HRI position: Below barcode
            GS + b'k' + b'\x04' + b'RMS-1234\x00' + # Print Barcode CODE39 (format 4 handles standard code39 well)
            b"\n\n\n" +
            b"--------------------------------\n" +
            ALIGN_CENTER + DOUBLE_HEIGHT + b"TEST COMPLETED\n" + NORMAL_TEXT +
            b"--------------------------------\n\n\n\n\n" +
            CUT + 
            # Send hardware self-test commands directly after the cut
            SELF_TEST + SELF_TEST_NETWORK
        )
        
        try:
            if conn_type == "USB/System":
                actual_printer = self.get_actual_printer_name(self.selected_printer.get())
                print_to_windows_spooler(actual_printer, test_data)
            else:
                print_to_network_ip(self.ip_entry.get() if self.ip_entry else "", self.port_entry.get() if self.port_entry else "9100", test_data)
            self.log(f"Extensive test print sent to {conn_type}")
        except Exception as e:
            self.log(f"Error: {e}")

    # def handle_network_config_push(self):
    #     current_ip = self.net_current_ip.get().strip()
    #     new_ip = self.net_new_ip.get().strip()
    #     mask = self.net_mask.get().strip()
    #     gateway = self.net_gateway.get().strip()
    # 
    #     if not current_ip or not new_ip or not mask or not gateway:
    #         self.log("Error: All fields are required to push network config.")
    #         return
    # 
    #     def ip_to_bytes(ip_str):
    #         try:
    #             return bytes(map(int, ip_str.split('.')))
    #         except:
    #             return b'\x00\x00\x00\x00'
    # 
    #     new_ip_b = ip_to_bytes(new_ip)
    #     mask_b = ip_to_bytes(mask)
    #     gateway_b = ip_to_bytes(gateway)
    # 
    #     # ESC/POS Network Config Commands (Standard GS ( E )
    #     GS = b'\x1d'
    #     
    #     # 1. Start Network Config mode
    #     # Function 112: GS ( E pL pH fn [parameter1] [parameter2]...
    #     CMD_IP = GS + b'(' + b'E' + b'\x06' + b'\x00' + b'\x70' + b'\x01' + new_ip_b
    #     CMD_MASK = GS + b'(' + b'E' + b'\x06' + b'\x00' + b'\x70' + b'\x02' + mask_b
    #     CMD_GATEWAY = GS + b'(' + b'E' + b'\x06' + b'\x00' + b'\x70' + b'\x03' + gateway_b
    #     CMD_APPLY = GS + b'(' + b'E' + b'\x03' + b'\x00' + b'\x71' + b'\x01' + b'\x00' # Function 113: Apply config
    # 
    #     # Alternate vendor commands (e.g., specific Chinese models)
    #     ALT_IP = b'\x1F\x1B\x1F\x91\x00\x04\x01' + new_ip_b
    #     ALT_MASK = b'\x1F\x1B\x1F\x91\x00\x04\x02' + mask_b
    #     ALT_GATEWAY = b'\x1F\x1B\x1F\x91\x00\x04\x03' + gateway_b
    # 
    #     config_payload = (
    #         CMD_IP + CMD_MASK + CMD_GATEWAY + CMD_APPLY + 
    #         ALT_IP + ALT_MASK + ALT_GATEWAY
    #     )
    # 
    #     self.log(f"Connecting to {current_ip} to push new IP: {new_ip}...")
    #     try:
    #         # Try TCP 9100 first
    #         print_to_network_ip(current_ip, "9100", config_payload)
    #         self.log(f"Success (TCP): Configuration sent! The printer ({new_ip}) should beep and restart in 3 seconds.")
    #     except Exception as e:
    #         self.log(f"TCP connection failed ({e}). Attempting UDP broadcast fallback...")
    #         try:
    #             # If TCP times out (common if subnet is entirely different), attempt UDP blast
    #             push_config_udp('255.255.255.255', config_payload) # Full broadcast mode
    #             push_config_udp(current_ip, config_payload) # Targeted UDP mode
    #             self.log(f"Success (UDP): Broadcast pushed. Check if printer ({new_ip}) restarts.")
    #         except Exception as e2:
    #             self.log(f"Fatal Error: Could not push via TCP or UDP. {e2}")

    # --- System Tray Integration ---
    def create_image(self):
        # Create a simple icon image for the system tray
        image = Image.new('RGB', (64, 64), color=(0, 120, 215))
        d = ImageDraw.Draw(image)
        d.text((10, 20), "RMS", fill=(255, 255, 255))
        return image

    def withdraw_window(self):
        self.withdraw()
        if not self.tray_icon:
            menu = (
                pystray.MenuItem('Show Settings', self.show_window, default=True),
                pystray.MenuItem('Quit Server', self.quit_window)
            )
            self.tray_icon = pystray.Icon("name", self.icon_image, "RMS Print Server", menu)
            import threading
            threading.Thread(target=self.tray_icon.run, daemon=True).start()

    def show_window(self, icon, item):
        icon.stop()
        self.tray_icon = None
        self.after(0, self.deiconify)

    def quit_window(self, icon, item):
        icon.stop()
        if self.pusher_client:
            try:
                self.pusher_client.disconnect()
            except:
                pass
        self.destroy()

if __name__ == "__main__":
    app = PrintServerUI()
    # Trigger withdraw instantly if start-on-boot is set and it was launched automatically,
    # but for simplicity we will just let it open by default unless we detect a flag.
    if len(sys.argv) > 1 and sys.argv[1] == "--hide":
        app.withdraw_window()
    app.mainloop()