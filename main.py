import customtkinter as ctk
import asyncio
import threading
import json
import base64
import win32print
import socket
import logging

# --- Logic for Different Printer Types ---
def print_to_windows_spooler(printer_name, raw_data):
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
        self.geometry("700x550")
        
        # Configuration Variables
        self.printers = [p[2] for p in win32print.EnumPrinters(win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS)]
        self.selected_printer = ctk.StringVar(value=self.printers[0] if self.printers else "")
        self.connection_type = ctk.StringVar(value="USB/System")

        self.setup_ui()
        
        # Start Reverb Thread
        threading.Thread(target=lambda: asyncio.run(self.reverb_listener()), daemon=True).start()

    def setup_ui(self):
        ctk.CTkLabel(self, text="Settings", font=("Arial", 16, "bold")).pack(pady=10)

        # Connection Type Toggle
        self.type_menu = ctk.CTkSegmentedButton(self, values=["USB/System", "Network IP"], 
                                                variable=self.connection_type, command=self.toggle_inputs)
        self.type_menu.pack(pady=5)

        # Container for Dynamic Inputs
        self.input_frame = ctk.CTkFrame(self)
        self.input_frame.pack(pady=10, padx=20, fill="x")
        self.refresh_inputs()

        # Log Window
        self.log_text = ctk.CTkTextbox(self, height=200)
        self.log_text.pack(pady=10, padx=20, fill="both", expand=True)
        
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
        # Your reverb logic here...
        pass

    def handle_test_print(self):
        test_data = b"\x1b\x40Test Print Successful\n\n\n\x1d\x56\x00"
        try:
            if self.connection_type.get() == "USB/System":
                print_to_windows_spooler(self.selected_printer.get(), test_data)
            else:
                print_to_network_ip(self.ip_entry.get(), self.port_entry.get(), test_data)
            self.log(f"Test print sent to {self.connection_type.get()}")
        except Exception as e:
            self.log(f"Error: {e}")

if __name__ == "__main__":
    app = PrintServerUI()
    app.mainloop()