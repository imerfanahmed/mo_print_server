import customtkinter as ctk
import json
import os
import sys
import threading
from PIL import Image, ImageDraw
import pystray
from datetime import datetime

import config
import printer
import autostart
from pusher_manager import PusherManager

class PrintServerUI(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Magic Office RMS Cloud Print Gateway")
        self.geometry("700x700")

        # Suppress harmless CustomTkinter resize TclErrors (widget destroyed mid-redraw)
        def _silent_tcl_error(exc, val, tb):
            if 'invalid command name' in str(val):
                return  # silently ignore stale widget redraws
            import traceback
            traceback.print_exception(exc, val, tb)
        self.report_callback_exception = _silent_tcl_error

        # System Tray Hook
        self.protocol('WM_DELETE_WINDOW', self.withdraw_window)
        self.icon_image = self.create_image()
        self.tray_icon = None
        
        # Discover Printers
        self.printers = []
        self.printer_mapping = {}
        if printer.win32print:
            for p in printer.win32print.EnumPrinters(printer.win32print.PRINTER_ENUM_LOCAL | printer.win32print.PRINTER_ENUM_CONNECTIONS):
                printer_name = p[2]
                display_name = printer_name
                try:
                    hprinter = printer.win32print.OpenPrinter(printer_name)
                    info = printer.win32print.GetPrinter(hprinter, 2)
                    share_name = info.get('pShareName', '')
                    printer.win32print.ClosePrinter(hprinter)
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
        self.pusher_config = {}
        try:
            config_path = config.get_config_path("pusher_config.json")
            if os.path.exists(config_path):
                with open(config_path, "r") as f:
                    self.pusher_config = json.load(f)
        except Exception:
            pass

        # Buzzer Config
        self.buzzer_enabled = ctk.BooleanVar(value=True)
        try:
            buzzer_config_path = config.get_config_path("buzzer_config.json")
            if os.path.exists(buzzer_config_path):
                with open(buzzer_config_path, "r") as f:
                    buzzer_data = json.load(f)
                    self.buzzer_enabled.set(buzzer_data.get("enabled", True))
        except Exception:
            pass

        # Initialize Pusher Manager
        self.pusher_manager = PusherManager(
            log_callback=self.log,
            status_callback=self.update_pusher_status,
            print_routing_callback=self.route_print_job,
            buzzer_enabled_callback=lambda: self.buzzer_enabled.get()
        )

        # Caller ID Server Config & State
        self.webserver = None
        self.webserver_enabled = ctk.BooleanVar(value=True)
        self.webserver_port = ctk.StringVar(value="8000")
        try:
            caller_id_config_path = config.get_config_path("caller_id_config.json")
            if os.path.exists(caller_id_config_path):
                with open(caller_id_config_path, "r") as f:
                    caller_id_data = json.load(f)
                    self.webserver_enabled.set(caller_id_data.get("enabled", True))
                    self.webserver_port.set(str(caller_id_data.get("port", "8000")))
        except Exception:
            pass

        self.setup_ui()
        
        # Connect to Pusher if config exists
        if self.pusher_config.get("key"):
            self.connect_to_pusher()

        # Start Caller ID Webserver if enabled
        if self.webserver_enabled.get():
            self.start_caller_id_server()

    def setup_ui(self):
        # Setup Tabs
        self.tabview = ctk.CTkTabview(self, height=350)
        self.tabview.pack(padx=20, pady=10, fill="x")

        self.tab_config = self.tabview.add("Print Server Config")
        self.tab_test = self.tabview.add("Test Print")
        self.tab_caller_id = self.tabview.add("Caller ID Server")

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
        
        self.autostart_var = ctk.BooleanVar(value=autostart.check_autostart())
        self.autostart_chk = ctk.CTkCheckBox(self.tab_config, text="Start Automatically on Windows Boot", variable=self.autostart_var, command=self.toggle_autostart)
        self.autostart_chk.pack(pady=5)

        # --- Buzzer / Drawer Settings ---
        self.buzzer_frame = ctk.CTkFrame(self.tab_config)
        self.buzzer_frame.pack(pady=10, padx=20, fill="x")
        
        self.buzzer_chk = ctk.CTkCheckBox(self.buzzer_frame, text="Enable Buzzer (Cash Drawer Port)", variable=self.buzzer_enabled, command=self.save_buzzer_config)
        self.buzzer_chk.pack(side="left", padx=15, pady=10)
        
        self.test_buzzer_btn = ctk.CTkButton(self.buzzer_frame, text="Test Buzzer", width=100, command=self.handle_test_buzzer)
        self.test_buzzer_btn.pack(side="right", padx=15, pady=10)

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

        # --- Caller ID Server Tab ---
        ctk.CTkLabel(self.tab_caller_id, text="Caller ID HTTP Webserver Settings", font=("Arial", 16, "bold")).pack(pady=(10, 5))
        
        self.caller_id_frame = ctk.CTkFrame(self.tab_caller_id)
        self.caller_id_frame.pack(pady=5, padx=20, fill="x")
        
        self.caller_id_enabled_chk = ctk.CTkCheckBox(self.caller_id_frame, text="Enable Caller ID Webserver", variable=self.webserver_enabled)
        self.caller_id_enabled_chk.pack(anchor="w", padx=15, pady=10)
        
        port_frame = ctk.CTkFrame(self.caller_id_frame, fg_color="transparent")
        port_frame.pack(fill="x", padx=15, pady=5)
        ctk.CTkLabel(port_frame, text="Webserver Port:").pack(side="left", padx=5)
        self.port_entry_caller_id = ctk.CTkEntry(port_frame, textvariable=self.webserver_port, width=80)
        self.port_entry_caller_id.pack(side="left", padx=5)

        url_frame = ctk.CTkFrame(self.caller_id_frame, fg_color="transparent")
        url_frame.pack(fill="x", padx=15, pady=5)
        ctk.CTkLabel(url_frame, text="Webhook URL:").pack(side="left", padx=5)
        self.webhook_url_var = ctk.StringVar()
        self.update_webhook_url_display()
        self.url_entry = ctk.CTkEntry(url_frame, textvariable=self.webhook_url_var, width=320, state="readonly")
        self.url_entry.pack(side="left", padx=5, fill="x", expand=True)
        self.copy_btn = ctk.CTkButton(url_frame, text="Copy URL", width=70, command=self.copy_webhook_url)
        self.copy_btn.pack(side="left", padx=5)
        
        self.save_restart_server_btn = ctk.CTkButton(self.tab_caller_id, text="Save & Restart Server", command=self.save_and_restart_caller_id_server)
        self.save_restart_server_btn.pack(pady=10)

        # --- Shared Log Window ---
        ctk.CTkLabel(self, text="Activity Log", font=("Arial", 14, "bold")).pack(pady=(10, 0))
        self.log_text = ctk.CTkTextbox(self)
        self.log_text.pack(pady=(5, 20), padx=20, fill="both", expand=True)

    def get_actual_printer_name(self, identifier):
        # 1. Exact match on display name key
        if identifier in self.printer_mapping:
            return self.printer_mapping[identifier]

        # 2. Exact match on raw Windows printer name value
        if identifier in self.printer_mapping.values():
            return identifier

        # 3. Case-insensitive partial match — e.g. "till2" matches "80mm Printer (till2)"
        identifier_lower = identifier.lower()
        for display_name, raw_name in self.printer_mapping.items():
            if identifier_lower in display_name.lower() or identifier_lower in raw_name.lower():
                self.log(f"Matched identifier '{identifier}' -> '{raw_name}'")
                return raw_name

        # 4. Last resort: use the printer selected in the GUI dropdown
        selected = self.selected_printer.get()
        fallback = self.printer_mapping.get(selected, selected)
        self.log(f"Warning: No printer matched identifier '{identifier}'. Falling back to selected: '{fallback}'")
        return fallback

    def toggle_autostart(self):
        enabled = self.autostart_var.get()
        success = autostart.set_autostart(enabled)
        if success:
            state = "enabled" if enabled else "disabled"
            self.log(f"Auto-startup on boot is now {state}.")
        else:
            self.autostart_var.set(not enabled)  # Revert UI
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
        return datetime.now().strftime("%H:%M:%S")

    def save_and_connect_pusher(self):
        text = self.pusher_config_text.get("0.0", "end").strip()
        config_data = {}
        for line in text.split('\n'):
            line = line.strip()
            if '=' in line:
                k, v = line.split('=', 1)
                k = k.strip()
                v = v.strip().strip('",\'')
                config_data[k] = v
        
        if "channel" not in config_data:
            config_data["channel"] = "print-channel"
            
        self.pusher_config = config_data
        try:
            with open(config.get_config_path("pusher_config.json"), "w") as f:
                json.dump(config_data, f)
            self.log("Configuration saved.")
        except Exception as e:
            self.log(f"Error saving config: {e}")

        self.connect_to_pusher()

    def save_buzzer_config(self):
        config_data = {
            "enabled": self.buzzer_enabled.get()
        }
        try:
            buzzer_config_path = config.get_config_path("buzzer_config.json")
            with open(buzzer_config_path, "w") as f:
                json.dump(config_data, f)
            self.log(f"Buzzer setting updated (Enabled: {config_data['enabled']})")
        except Exception as e:
            self.log(f"Error saving buzzer config: {e}")

    def handle_test_buzzer(self):
        buzzer_cmd = printer.get_buzzer_command()
        conn_type = self.connection_type.get()
        
        try:
            if conn_type == "USB/System":
                target_printer = self.selected_printer.get()
                if not target_printer:
                    self.log("Error: No printer selected.")
                    return
                actual_printer = self.get_actual_printer_name(target_printer)
                printer.print_to_windows_spooler(actual_printer, buzzer_cmd)
                self.log(f"Sent cash drawer pulse (Buzzer) to USB printer: {actual_printer}")
            else:
                ip = self.ip_entry.get().strip() if hasattr(self, 'ip_entry') and self.ip_entry else ""
                port = self.port_entry.get().strip() if hasattr(self, 'port_entry') and self.port_entry else "9100"
                if not ip:
                    self.log("Error: IP address is required to test network buzzer.")
                    return
                printer.print_to_network_ip(ip, port, buzzer_cmd)
                self.log(f"Sent cash drawer pulse (Buzzer) to network printer: {ip}:{port}")
        except Exception as e:
            self.log(f"Error testing buzzer: {e}")

    def connect_to_pusher(self):
        key = self.pusher_config.get("key")
        cluster = self.pusher_config.get("cluster", "eu")
        channel = self.pusher_config.get("channel", "print-channel")
        self.pusher_manager.connect(key, cluster, channel)

    def update_pusher_status(self, text, color):
        self.pusher_status_lbl.configure(text=text, text_color=color)
        if "Connected" in text:
            self.pusher_connected = True
        else:
            self.pusher_connected = False

    def route_print_job(self, payload, content_to_print):
        printer.route_print_job(
            payload=payload,
            content_to_print=content_to_print,
            log_callback=self.log,
            get_actual_printer_name_fn=self.get_actual_printer_name
        )

    def handle_test_print(self):
        conn_type = self.connection_type.get()
        if conn_type == "USB/System":
            target_name = self.selected_printer.get()
        else:
            ip = self.ip_entry.get() if hasattr(self, 'ip_entry') and self.ip_entry else ""
            port = self.port_entry.get() if hasattr(self, 'port_entry') and self.port_entry else "9100"
            target_name = f"{ip}:{port}"

        try:
            time_str = self.get_time()
            test_data = printer.generate_test_print_payload(
                target_name=target_name,
                conn_type=conn_type,
                buzzer_enabled=self.buzzer_enabled.get(),
                time_str=time_str
            )
            
            if conn_type == "USB/System":
                actual_printer = self.get_actual_printer_name(self.selected_printer.get())
                printer.print_to_windows_spooler(actual_printer, test_data)
            else:
                ip_val = self.ip_entry.get() if self.ip_entry else ""
                port_val = self.port_entry.get() if self.port_entry else "9100"
                printer.print_to_network_ip(ip_val, port_val, test_data)
                
            self.log(f"Extensive test print sent to {conn_type}")
        except Exception as e:
            self.log(f"Error: {e}")

    # --- System Tray Integration ---
    def create_image(self):
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
            threading.Thread(target=self.tray_icon.run, daemon=True).start()

    def show_window(self, icon, item):
        icon.stop()
        self.tray_icon = None
        self.after(0, self.deiconify)

    def start_caller_id_server(self):
        try:
            port = int(self.webserver_port.get().strip())
        except ValueError:
            self.log("Invalid Caller ID Server port. Defaulting to 8000.")
            port = 8000
            self.webserver_port.set("8000")

        from caller_id_server import CallerIDWebServer
        self.webserver = CallerIDWebServer(
            port=port,
            caller_id_callback=self.handle_incoming_call,
            log_callback=self.log
        )
        self.webserver.start()

    def handle_incoming_call(self, tel):
        self.log(f"Caller ID Webserver: Incoming call detected for {tel}")
        threading.Thread(target=self.pusher_manager.trigger_call_event, args=(tel,), daemon=True).start()

    def update_webhook_url_display(self):
        port_val = self.webserver_port.get().strip() or "8000"
        self.webhook_url_var.set(f"http://localhost:{port_val}/incoming_call")

    def copy_webhook_url(self):
        url = self.webhook_url_var.get()
        self.clipboard_clear()
        self.clipboard_append(url)
        self.update()
        self.log(f"Copied Caller ID URL: {url}")

    def save_and_restart_caller_id_server(self):
        # Save configuration
        config_data = {
            "enabled": self.webserver_enabled.get(),
            "port": self.webserver_port.get().strip()
        }
        try:
            caller_id_config_path = config.get_config_path("caller_id_config.json")
            with open(caller_id_config_path, "w") as f:
                json.dump(config_data, f)
            self.log("Caller ID server configuration saved.")
        except Exception as e:
            self.log(f"Error saving Caller ID server config: {e}")

        self.update_webhook_url_display()

        # Stop existing server if running
        if self.webserver:
            self.webserver.stop()
            self.webserver = None

        # Start new server if enabled
        if self.webserver_enabled.get():
            self.start_caller_id_server()
        else:
            self.log("Caller ID Webserver has been disabled.")

    def quit_window(self, icon, item):
        icon.stop()
        self.pusher_manager.disconnect()
        if self.webserver:
            self.webserver.stop()
        self.destroy()
