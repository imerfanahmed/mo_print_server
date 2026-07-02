import threading
import urllib.parse
import json
from http.server import BaseHTTPRequestHandler, HTTPServer

class CallerIDRequestHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Silence default standard output request logs to keep console clean
        pass

    def do_GET(self):
        self._handle_request()

    def do_POST(self):
        self._handle_request()

    def _handle_request(self):
        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path

        # Handle requests to /incoming_call, /call, or /
        if path in ('/incoming_call', '/call', '/'):
            tel = None
            
            # 1. Check GET query parameters
            query_params = urllib.parse.parse_qs(parsed_url.query)
            if 'tel' in query_params:
                tel = query_params['tel'][0]
                
            # 2. Check POST body if not found in URL parameters
            if not tel and self.command == 'POST':
                try:
                    content_length = int(self.headers.get('Content-Length', 0))
                    if content_length > 0:
                        body = self.rfile.read(content_length).decode('utf-8')
                        try:
                            # Try JSON parsing
                            post_data = json.loads(body)
                            tel = post_data.get('tel')
                        except json.JSONDecodeError:
                            # Try parsing as form parameters
                            post_data = urllib.parse.parse_qs(body)
                            if 'tel' in post_data:
                                tel = post_data['tel'][0]
                except Exception:
                    pass

            if tel:
                cleaned_tel = str(tel).strip()
                # Run the callback on the server object
                if hasattr(self.server, 'caller_id_callback') and self.server.caller_id_callback:
                    self.server.caller_id_callback(cleaned_tel)
                
                html_response = f"""<!DOCTYPE html>
<html>
<head>
    <title>Call Received</title>
    <meta charset="utf-8">
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            text-align: center;
            padding-top: 100px;
            background-color: #0f172a;
            color: #f8fafc;
            margin: 0;
        }}
        .card {{
            display: inline-block;
            padding: 40px;
            background: #1e293b;
            border-radius: 12px;
            box-shadow: 0 10px 15px -3px rgba(0,0,0,0.3);
            border: 1px solid #334155;
        }}
        h1 {{
            color: #10b981;
            margin-top: 0;
            font-size: 24px;
        }}
        p {{
            font-size: 16px;
            color: #94a3b8;
        }}
        .tel {{
            font-size: 24px;
            font-weight: bold;
            color: #38bdf8;
            margin: 20px 0;
        }}
    </style>
</head>
<body>
    <div class="card">
        <h1>Call Received Successfully</h1>
        <p>Caller Number:</p>
        <div class="tel">{cleaned_tel}</div>
        <p>This window will close automatically...</p>
    </div>
    <script type="text/javascript">
        setTimeout(function() {{
            window.close();
        }}, 800);
    </script>
</body>
</html>"""
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(html_response.encode('utf-8'))
            else:
                self.send_response(400)
                self.send_header('Content-Type', 'text/plain')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(b"Error: Missing 'tel' parameter in request.")
        else:
            self.send_response(404)
            self.send_header('Content-Type', 'text/plain')
            self.end_headers()
            self.wfile.write(b"Not Found")

class CallerIDHTTPServer(HTTPServer):
    def __init__(self, server_address, RequestHandlerClass, caller_id_callback):
        super().__init__(server_address, RequestHandlerClass)
        self.caller_id_callback = caller_id_callback

class CallerIDWebServer:
    def __init__(self, port, caller_id_callback, log_callback):
        self.port = port
        self.caller_id_callback = caller_id_callback
        self.log_callback = log_callback
        self.server = None
        self.thread = None
        self.is_running = False

    def start(self):
        if self.is_running:
            self.stop()

        try:
            # Bind to 0.0.0.0 to allow incoming requests from external sources on the local network
            self.server = CallerIDHTTPServer(('0.0.0.0', self.port), CallerIDRequestHandler, self.caller_id_callback)
            self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
            self.thread.start()
            self.is_running = True
            self.log_callback(f"Caller ID Webserver started. Send calls to: http://[PC_IP_ADDRESS]:{self.port}/incoming_call?tel=NUMBER")
            return True
        except Exception as e:
            self.log_callback(f"Failed to start Caller ID Webserver on port {self.port}: {e}")
            self.server = None
            self.thread = None
            self.is_running = False
            return False

    def stop(self):
        if self.server:
            self.log_callback("Stopping Caller ID Webserver...")
            server_ref = self.server
            def shutdown_worker():
                server_ref.shutdown()
                server_ref.server_close()
            threading.Thread(target=shutdown_worker, daemon=True).start()
            
            self.server = None
            self.thread = None
            self.is_running = False
