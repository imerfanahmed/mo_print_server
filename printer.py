import socket
import sys

try:
    import win32print
except ImportError:
    win32print = None

# --- Spooler / Direct TCP / UDP Config Senders ---

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
        s.settimeout(3)  # Reduce timeout so config push fails fast if TCP doesn't work
        s.connect((ip_address, int(port)))
        s.sendall(raw_data)

def push_config_udp(ip_address, raw_data):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s.sendto(raw_data, (ip_address, 40000))
        s.sendto(raw_data, (ip_address, 9100))

def get_buzzer_command():
    # ESC p m t1 t2
    # We send to both pin 2 (m=0) and pin 5 (m=1) to ensure the connected buzzer sounds
    # Pulse: 50ms on (25 * 2ms), 500ms off (250 * 2ms)
    pin2_pulse = b'\x1b\x70\x00\x19\xfa'
    pin5_pulse = b'\x1b\x70\x01\x19\xfa'
    return pin2_pulse + pin5_pulse

def normalize_encoding(raw_bytes):
    """
    Prepends ESC t 0 (PC437 code page selection) to the ESC/POS byte stream.
    The PHP server already generates bytes in PC437 encoding where
    £ = 0x9C, € = 0xEE, etc. We just need to ensure the printer is
    explicitly in PC437 mode before receiving the data.
    No byte conversion is performed — the content is passed through unchanged.
    """
    ESC_SELECT_CODEPAGE_PC437 = b'\x1b\x74\x00'  # ESC t 0 = PC437
    return ESC_SELECT_CODEPAGE_PC437 + raw_bytes

# --- ESC/POS Test Sheet Generator ---

def generate_test_print_payload(target_name, conn_type, buzzer_enabled, time_str):
    ESC = b'\x1b'
    GS = b'\x1d'
    
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

    chars = "".join(chr(i) for i in range(33, 127))
    char_map = b""
    for i in range(0, len(chars), 32):
        char_map += chars[i:i+32].encode('ascii') + b"\n"

    img_size = 200
    from PIL import Image, ImageDraw
    test_img = Image.new('1', (img_size, img_size), 1)  # 1-bit monochrome, white background
    draw = ImageDraw.Draw(test_img)
    draw.rectangle([10, 10, img_size-10, img_size-10], outline=0, width=5)
    draw.line([10, 10, img_size-10, img_size-10], fill=0, width=5)
    draw.line([img_size-10, 10, 10, img_size-10], fill=0, width=5)
    
    img_bytes = test_img.tobytes()
    width_bytes = img_size // 8
    height_dots = img_size
    
    IMG_CMD = GS + b'v0' + b'\x00' + \
              bytes([width_bytes % 256, width_bytes // 256]) + \
              bytes([height_dots % 256, height_dots // 256]) + \
              img_bytes

    SELF_TEST = GS + b'(' + b'A' + b'\x02' + b'\x00' + b'\x00' + b'\x02'
    SELF_TEST_NETWORK = GS + b'\x28' + b'\x45' + b'\x02' + b'\x00' + b'\x01' + b'\x49'

    test_data = (
        INIT + SET_80MM_WIDTH +
        ALIGN_CENTER + INVERT_ON + b"   MAGIC OFFICE RMS SELF-TEST   \n" + INVERT_OFF + b"\n" +
        ALIGN_LEFT + NORMAL_TEXT +
        b"Printer Diagnostics & Configuration\n" +
        b"--------------------------------\n" +
        b"Target: " + target_name.encode('utf-8') + b"\n" +
        b"Connection: " + conn_type.encode('utf-8') + b"\n" +
        b"Time: " + time_str.encode('utf-8') + b"\n" +
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
        GS + b'h' + chr(80).encode('ascii') +
        GS + b'w' + chr(2).encode('ascii') +
        GS + b'f' + chr(0).encode('ascii') +
        GS + b'H' + chr(2).encode('ascii') +
        GS + b'k' + b'\x04' + b'RMS-1234\x00' +
        b"\n\n\n" +
        b"--------------------------------\n" +
        ALIGN_CENTER + DOUBLE_HEIGHT + b"TEST COMPLETED\n" + NORMAL_TEXT +
        b"--------------------------------\n\n\n\n\n" +
        CUT + 
        SELF_TEST + SELF_TEST_NETWORK
    )
    
    if buzzer_enabled:
        test_data += get_buzzer_command()
        
    return test_data

# --- Print Job Routing Dispatcher ---

def route_print_job(payload, content_to_print, log_callback, get_actual_printer_name_fn):
    if isinstance(payload, dict):
        conn_type = payload.get("connectivity")
        printer_target = payload.get("printer")

        log_callback(f"Routing to -> Connection: {conn_type}, Target: {printer_target}")

        try:
            if conn_type in ("network", "ip") and printer_target:
                if ":" in printer_target:
                    ip_target, port_target = printer_target.split(":", 1)
                else:
                    ip_target = printer_target
                    port_target = "9100"
                print_to_network_ip(ip_target, port_target, content_to_print)
                log_callback(f"Success: Print job sent to network printer: {printer_target}")
            elif conn_type == "usb" and printer_target:
                actual_printer = get_actual_printer_name_fn(printer_target)
                print_to_windows_spooler(actual_printer, content_to_print)
                log_callback(f"Success: Print job sent to USB/System printer: {actual_printer}")
            else:
                log_callback(f"Error: Invalid payload format or missing destination. Connectivity: '{conn_type}', Printer: '{printer_target}'")
        except Exception as e:
            log_callback(f"Error sending print job to hardware: {e}")
    else:
        log_callback("Error: Payload is not a dictionary. Cannot determine printer destination.")
