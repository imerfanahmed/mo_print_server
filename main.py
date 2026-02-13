import customtkinter as ctk
import asyncio
import threading
import json
import base64
import socket
import logging
import websockets
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

# --- UI Application ---
class PrintServerUI(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("RMS Cloud Print Gateway")
        self.geometry("700x700")
        
        # Configuration Variables
        if win32print:
            self.printers = [p[2] for p in win32print.EnumPrinters(win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS)]
        else:
            self.printers = ["Mock Printer 1", "Mock Printer 2"]
            
        self.selected_printer = ctk.StringVar(value=self.printers[0] if self.printers else "")
        self.connection_type = ctk.StringVar(value="USB/System")
        
        # Reverb Config
        self.reverb_host = ctk.StringVar(value="localhost")
        self.reverb_port = ctk.StringVar(value="8080")
        self.reverb_app_key = ctk.StringVar(value="")
        self.reverb_channel = ctk.StringVar(value="print-channel")
        self.reverb_scheme = ctk.StringVar(value="ws")
        self.reverb_connected = False

        self.setup_ui()
        
        # Start Reverb Thread
        threading.Thread(target=lambda: asyncio.run(self.reverb_listener()), daemon=True).start()

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
        
        # Reverb Settings Header
        ctk.CTkLabel(self, text="Cloud Connection (Reverb)", font=("Arial", 16, "bold")).pack(pady=(15, 5))
        
        # Reverb Config Frame
        self.reverb_frame = ctk.CTkFrame(self)
        self.reverb_frame.pack(pady=5, padx=20, fill="x")
        
        # Host & Port
        row1 = ctk.CTkFrame(self.reverb_frame, fg_color="transparent")
        row1.pack(fill="x", padx=5, pady=2)
        ctk.CTkLabel(row1, text="Host:").pack(side="left", padx=5)
        ctk.CTkEntry(row1, textvariable=self.reverb_host, width=200).pack(side="left", padx=5)
        ctk.CTkLabel(row1, text="Port:").pack(side="left", padx=5)
        ctk.CTkEntry(row1, textvariable=self.reverb_port, width=60).pack(side="left", padx=5)
        ctk.CTkLabel(row1, text="Scheme:").pack(side="left", padx=5)
        ctk.CTkOptionMenu(row1, variable=self.reverb_scheme, values=["ws", "wss"], width=70).pack(side="left", padx=5)

        # App Key & Channel
        row2 = ctk.CTkFrame(self.reverb_frame, fg_color="transparent")
        row2.pack(fill="x", padx=5, pady=2)
        ctk.CTkLabel(row2, text="App Key:").pack(side="left", padx=5)
        ctk.CTkEntry(row2, textvariable=self.reverb_app_key, width=150).pack(side="left", padx=5)
        ctk.CTkLabel(row2, text="Channel:").pack(side="left", padx=5)
        ctk.CTkEntry(row2, textvariable=self.reverb_channel, width=150).pack(side="left", padx=5)

        self.reverb_status_lbl = ctk.CTkLabel(self, text="Status: Disconnected", text_color="red")
        self.reverb_status_lbl.pack(pady=5)

        # Log Window
        ctk.CTkLabel(self, text="Activity Log", font=("Arial", 14, "bold")).pack(pady=(10, 0))
        self.log_text = ctk.CTkTextbox(self, height=150)
        self.log_text.pack(pady=5, padx=20, fill="both", expand=True)
        
        self.test_btn = ctk.CTkButton(self, text="Send Test Print", fg_color="green", command=self.handle_test_print)
        self.test_btn.pack(pady=10)

    def toggle_inputs(self, value):
        for widget in self.input_frame.winfo_children():
            widget.destroy()
        self.refresh_inputs()

    def refresh_inputs(self):
        if self.connection_type.get() == "USB/System":
            ctk.CTkLabel(self.input_frame, text="Select Installed Printer:").pack(side="left", padx=5)
            self.p_menu = ctk.CTkOptionMenu(self.input_frame, values=self.printers, variable=self.selected_printer)
            self.p_menu.pack(side="left", padx=5)
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

    async def reverb_listener(self):
        while True:
            try:
                # Construct WebSocket URL
                host = self.reverb_host.get()
                port = self.reverb_port.get()
                app_key = self.reverb_app_key.get()
                scheme = self.reverb_scheme.get()
                
                if not host or not app_key:
                    await asyncio.sleep(2)
                    continue

                uri = f"{scheme}://{host}:{port}/app/{app_key}?protocol=7&client=python&version=1.0&flash=false"
                self.log(f"Connecting to Reverb: {uri}")
                
                async with websockets.connect(uri) as websocket:
                    self.reverb_connected = True
                    self.reverb_status_lbl.configure(text="Status: Connected", text_color="green")
                    self.log("Connected to Reverb")

                    async for message in websocket:
                        data = json.loads(message)
                        event = data.get('event')

                        if event == 'pusher:connection_established':
                            # Subscribe to channel
                            channel = self.reverb_channel.get()
                            subscribe_msg = {
                                "event": "pusher:subscribe",
                                "data": {"channel": channel}
                            }
                            await websocket.send(json.dumps(subscribe_msg))
                            self.log(f"Subscribed to channel: {channel}")

                        elif event == 'pusher:ping':
                            await websocket.send(json.dumps({"event": "pusher:pong"}))
                        
                        elif event and event not in ['pusher_internal:subscription_succeeded', 'pusher:pong']:
                            # Handle Print Event
                            try:
                                payload = data.get('data', '')
                                if isinstance(payload, str):
                                    payload = json.loads(payload)
                                
                                # Access raw data assumed to be in 'data' field or direct payload
                                raw_print_data = None
                                if 'data' in payload:
                                     raw_print_data = payload['data']
                                else:
                                     # If the payload itself is the data or it's just raw bytes in the event
                                     pass 
                                
                                # For this implementation, let's assume the event data contains 'print_data' 
                                # or we just take the raw string if it's not JSON
                                
                                # Adjustment based on user request "direct raw printing data"
                                # The event data field from Pusher is a stringified JSON.
                                # Inside that JSON should be our print content.
                                # Let's try to extract 'content' or use the whole body if valid.
                                
                                content_to_print = None
                                
                                # If payload is a dictionary, look for common keys
                                if isinstance(payload, dict):
                                    if 'content' in payload:
                                        content_to_print = payload['content']
                                    elif 'data' in payload:
                                        content_to_print = payload['data']
                                    else:
                                        # Fallback: dump the whole dict if it looks like raw data? 
                                        # Or maybe expected base64?
                                        # Let's assume it's a string in 'data' key for now
                                        content_to_print = payload.get('message', '')

                                if content_to_print:
                                    # Decode if base64 (optional safety check)
                                    try:
                                        decoded = base64.b64decode(content_to_print)
                                        content_to_print = decoded
                                    except:
                                        pass # Not base64, use as-is (encode to bytes)
                                        if isinstance(content_to_print, str):
                                            content_to_print = content_to_print.encode('utf-8')

                                    self.log(f"Received print job via Reverb ({len(content_to_print)} bytes)")
                                    
                                    if self.connection_type.get() == "USB/System":
                                        print_to_windows_spooler(self.selected_printer.get(), content_to_print)
                                    else:
                                        print_to_network_ip(self.ip_entry.get(), self.port_entry.get(), content_to_print)
                                else:
                                    self.log(f"Received event {event} but found no printable content")

                            except Exception as e:
                                self.log(f"Error processing Reverb message: {e}")

            except Exception as e:
                self.reverb_connected = False
                self.reverb_status_lbl.configure(text="Status: Disconnected", text_color="red")
                self.log(f"Reverb connection error: {e}. Retrying in 5s...")
                await asyncio.sleep(5)

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