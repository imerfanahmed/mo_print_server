import json
import base64
import pysher
import urllib.request
import urllib.error
import printer
import threading
import config

class PusherManager:
    def __init__(self, log_callback, status_callback, print_routing_callback, buzzer_enabled_callback):
        self.log_callback = log_callback
        self.status_callback = status_callback
        self.print_routing_callback = print_routing_callback
        self.buzzer_enabled_callback = buzzer_enabled_callback
        self.client = None
        self.connected = False

    def connect(self, key, cluster, channel_name):
        self.disconnect()

        if not key:
            self.log_callback("Pusher key missing. Cannot connect.")
            return

        self.log_callback(f"Initializing Pusher client (Cluster: {cluster})...")
        self.client = pysher.Pusher(key, cluster=cluster)

        # Attach connection handler
        self.client.connection.bind('pusher:connection_established', lambda data: self.on_connected(data, channel_name))
        self.client.connection.bind('pusher:connection_failed', self.on_failed)
        self.client.connection.bind('pusher:error', self.on_error)

        self.client.connect()

    def disconnect(self):
        if self.client:
            try:
                self.client.disconnect()
            except:
                pass
            self.client = None
        self.connected = False

    def on_connected(self, data, channel_name):
        self.connected = True
        self.status_callback("Status: Connected", "green")
        self.log_callback("Connected to Pusher successfully.")
        
        channel = self.client.subscribe(channel_name)
        channel.bind('App\\Events\\PrintJobReceived', self.handle_print_event)
        channel.bind('App\\Events\\PrintJobDispatched', self.handle_print_event)
        channel.bind('print-event', self.handle_print_event)
        channel.bind('print', self.handle_print_event)
        self.log_callback(f"Subscribed to channel: {channel_name}")

    def on_failed(self, data):
        self.connected = False
        self.status_callback("Status: Connection Failed", "red")
        self.log_callback(f"Pusher connection failed: {data}")

    def on_error(self, data):
        self.log_callback(f"Pusher Error: {data}")

    def play_pc_beep(self, audio_options):
        if not isinstance(audio_options, dict):
            return
        beep = audio_options.get('beep', False)
        if beep:
            beep_count = audio_options.get('beep_count', 1)
            try:
                beep_count = int(beep_count)
            except:
                beep_count = 1

            self.log_callback(f"Playing beep alert ({beep_count} times)...")

            def beep_worker():
                import time
                import os
                import ctypes

                beep_path = os.path.join(config.get_base_path(), "beep.mp3")

                def play_mp3_once(path):
                    """Play an MP3 using Windows MCI — no extra dependencies."""
                    winmm = ctypes.windll.winmm
                    safe_path = str(path).replace('/', '\\')
                    winmm.mciSendStringW(f'open "{safe_path}" type mpegvideo alias beep_snd', None, 0, None)
                    winmm.mciSendStringW('play beep_snd wait', None, 0, None)
                    winmm.mciSendStringW('close beep_snd', None, 0, None)

                def play_fallback():
                    import winsound
                    winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)

                for _ in range(beep_count):
                    try:
                        if os.path.isfile(beep_path):
                            play_mp3_once(beep_path)
                        else:
                            self.log_callback(f"beep.mp3 not found at {beep_path}, using system beep.")
                            play_fallback()
                    except Exception as e:
                        self.log_callback(f"Error playing beep: {e}")
                        try:
                            play_fallback()
                        except:
                            pass
                    time.sleep(0.2)

            threading.Thread(target=beep_worker, daemon=True).start()

    def parse_payload_data(self, data):
        """
        Parses print payload data.
        Returns: (routing_payload, content_bytes, audio_options)
        """
        if not isinstance(data, dict):
            return None, None, None

        # Check for new style format: {"success": true, "content": {"printer": ..., "payload": ...}}
        target_dict = None
        if 'content' in data and isinstance(data['content'], dict):
            if 'payload' in data['content']:
                target_dict = data['content']
        elif 'payload' in data and isinstance(data['payload'], dict):
            target_dict = data

        if target_dict:
            printer_info = target_dict.get('printer', {})
            payload_info = target_dict.get('payload', {})
            options_info = target_dict.get('options', {})
            audio_options = options_info.get('audio', {})

            connectivity = printer_info.get('connectivity', 'usb')
            if connectivity == 'usb':
                # Prefer identifier for local printer matching; fall back to name
                printer_target = printer_info.get('identifier') or printer_info.get('name')
            else:
                ip = printer_info.get('ip') or printer_info.get('identifier') or printer_info.get('name')
                port = printer_info.get('port', 9100)
                printer_target = f"{ip}:{port}"

            routing_payload = {
                'connectivity': connectivity,
                'printer': printer_target
            }

            enc = payload_info.get('encoding', '')
            raw_content = payload_info.get('content', '')
            
            content_bytes = None
            try:
                if enc == 'gzip+base64':
                    import zlib
                    import re
                    compressed = base64.b64decode(raw_content)
                    d = zlib.decompressobj(16 + zlib.MAX_WBITS)
                    decompressed_text = d.decompress(compressed).decode('utf-8', errors='ignore')
                    
                    # Clean and pad the inner base64 string
                    cleaned_b64 = re.sub(r'[^a-zA-Z0-9+/=]', '', decompressed_text)
                    missing_padding = len(cleaned_b64) % 4
                    if missing_padding:
                        cleaned_b64 += '=' * (4 - missing_padding)
                    content_bytes = base64.b64decode(cleaned_b64)
                elif enc == 'base64':
                    content_bytes = base64.b64decode(raw_content)
                else:
                    content_bytes = raw_content.encode('utf-8') if isinstance(raw_content, str) else raw_content
            except Exception as e:
                self.log_callback(f"Error decoding payload (encoding: {enc}): {e}")
                return None, None, None

            return routing_payload, content_bytes, audio_options

        else:
            # Old style format fallback
            content_to_print = None
            if 'content' in data:
                content_to_print = data['content']
            elif 'data' in data:
                content_to_print = data['data']
            else:
                content_to_print = data.get('message', '')

            content_bytes = None
            if content_to_print:
                try:
                    content_bytes = base64.b64decode(content_to_print)
                except:
                    content_bytes = content_to_print.encode('utf-8') if isinstance(content_to_print, str) else content_to_print

            routing_payload = {
                'connectivity': data.get('connectivity'),
                'printer': data.get('printer')
            }
            
            audio_options = {
                'beep': data.get('beep', False) or data.get('audio_beep', False),
                'beep_count': data.get('beep_count', 1)
            }
            return routing_payload, content_bytes, audio_options

    def handle_print_event(self, *args, **kwargs):
        try:
            if not args:
                return

            raw_data = args[0]
            if isinstance(raw_data, str):
                payload = json.loads(raw_data)
            else:
                payload = raw_data

            # Unwrap double nested Pusher envelopes if needed
            if isinstance(payload, dict) and 'data' in payload and isinstance(payload['data'], dict):
                inner = payload['data']
                if 'printer' in inner or 'connectivity' in inner or 'payload_url' in inner or 'is_v2' in inner:
                    payload = inner

            # --- LOG THE FULL PAYLOAD EVENT ---
            self.log_callback(f"--- INCOMING EVENT FROM PUSHER ---")
            if isinstance(payload, dict):
                log_payload = {k: v for k, v in payload.items() if k not in ['content', 'data', 'message']}
                if 'content' in payload or 'data' in payload:
                    log_payload['content'] = "[...base64 content truncated for log readability...]"
                self.log_callback(json.dumps(log_payload, indent=2))
            else:
                self.log_callback(str(payload))
            self.log_callback("----------------------------------")

            routing_payload = None
            content_bytes = None
            audio_options = None

            payload_url = payload.get('payload_url') if isinstance(payload, dict) else None

            if payload_url:
                self.log_callback(f"Fetching print payload from server: {payload_url}")
                import ssl
                try:
                    ssl_context = ssl._create_unverified_context()
                    req = urllib.request.Request(
                        payload_url, 
                        headers={'User-Agent': 'RMS-Print-Server/2.0'}
                    )
                    with urllib.request.urlopen(req, timeout=10, context=ssl_context) as response:
                        response_bytes = response.read()
                    
                    if response_bytes.startswith(b'\x1f\x8b'):
                        import zlib
                        try:
                            d_http = zlib.decompressobj(16 + zlib.MAX_WBITS)
                            response_bytes = d_http.decompress(response_bytes)
                        except Exception as e:
                            self.log_callback(f"Failed to decompress gzipped HTTP response: {e}")
                    
                    try:
                        fetched_json = json.loads(response_bytes.decode('utf-8'))
                        routing_payload, content_bytes, audio_options = self.parse_payload_data(fetched_json)
                        if content_bytes is None:
                            self.log_callback("Error: Decoded print content is empty or invalid.")
                            # Do not print raw JSON if decoding failed
                            return
                    except Exception:
                        content_bytes = response_bytes
                    
                    self.log_callback(f"Successfully fetched print job payload ({len(response_bytes)} bytes).")
                except urllib.error.URLError as e:
                    self.log_callback(f"Error fetching payload from server: {e}")
                    return
                except Exception as e:
                    self.log_callback(f"Unexpected error fetching payload: {e}")
                    return
            else:
                routing_payload, content_bytes, audio_options = self.parse_payload_data(payload)

            if content_bytes:
                # Log a preview of the print text
                try:
                    readable_text = ''.join(chr(b) if 32 <= b <= 126 or b == 10 else '' for b in content_bytes)
                    self.log_callback(f"Print job parsed successfully ({len(content_bytes)} bytes)")
                    self.log_callback(f"--- Print Data Preview ---\n{readable_text.strip()}\n----------------------------")
                except Exception as e:
                    self.log_callback(f"Received print job ({len(content_bytes)} bytes)")

                # Play PC hardware beep alert if configured
                if audio_options:
                    self.play_pc_beep(audio_options)

                # Append drawer buzzer command if checked in GUI
                if self.buzzer_enabled_callback():
                    buzzer_cmd = printer.get_buzzer_command()
                    content_bytes += buzzer_cmd
                    self.log_callback("Appended cash drawer port buzzer pulse to payload.")

                # If we don't have a structured routing_payload (e.g. raw fallback), build a simple one
                if not routing_payload:
                    routing_payload = {
                        'connectivity': payload.get('connectivity') if isinstance(payload, dict) else 'usb',
                        'printer': payload.get('printer') if isinstance(payload, dict) else None
                    }

                # Normalize encoding: convert UTF-8 to Windows-1252 for thermal printer
                # This fixes characters like £ (UTF-8: 0xC2 0xA3) printing as "Â£"
                content_bytes = printer.normalize_encoding(content_bytes)

                # Route print job
                self.print_routing_callback(routing_payload, content_bytes)
            else:
                self.log_callback("Error: Received event but found no printable content")

        except Exception as e:
            self.log_callback(f"Error processing Pusher event: {e}")

    def trigger_call_event(self, tel):
        import os
        try:
            config_path = config.get_config_path("pusher_config.json")
            if not os.path.exists(config_path):
                self.log_callback("Pusher configuration file not found. Cannot trigger call event.")
                return False
            with open(config_path, "r") as f:
                pusher_config = json.load(f)
        except Exception as e:
            self.log_callback(f"Failed to read pusher config: {e}")
            return False

        app_id = pusher_config.get("app_id")
        key = pusher_config.get("key")
        secret = pusher_config.get("secret")
        cluster = pusher_config.get("cluster", "eu")
        channel = pusher_config.get("channel", "print-channel")

        if not all([app_id, key, secret, channel]):
            self.log_callback("Missing Pusher credentials in config. Cannot trigger call event.")
            return False

        import time
        import hmac
        import hashlib
        import urllib.request
        import urllib.error

        event_name = 'call'
        data_dict = {
            'tel': tel
        }

        try:
            body = json.dumps({
                "name": event_name,
                "channels": [channel],
                "data": json.dumps(data_dict)
            })

            path = f"/apps/{app_id}/events"
            timestamp = str(int(time.time()))
            auth_version = "1.0"
            content_md5 = hashlib.md5(body.encode('utf-8')).hexdigest()

            string_to_sign = f"POST\n{path}\nauth_key={key}&auth_timestamp={timestamp}&auth_version={auth_version}&body_md5={content_md5}"
            signature = hmac.new(secret.encode('utf-8'), string_to_sign.encode('utf-8'), hashlib.sha256).hexdigest()

            url = f"https://api-{cluster}.pusher.com{path}?auth_key={key}&auth_timestamp={timestamp}&auth_version={auth_version}&body_md5={content_md5}&auth_signature={signature}"

            self.log_callback(f"Triggering call event to Pusher channel '{channel}' for number: {tel}")

            req = urllib.request.Request(
                url,
                data=body.encode('utf-8'),
                headers={"Content-Type": "application/json"}
            )
            
            import ssl
            ssl_context = ssl._create_unverified_context()
            with urllib.request.urlopen(req, timeout=10, context=ssl_context) as response:
                response.read()
            self.log_callback("Pusher call event triggered successfully.")
            return True
        except urllib.error.HTTPError as e:
            self.log_callback(f"HTTP Error triggering call event: {e.code} - {e.read().decode('utf-8')}")
            return False
        except Exception as e:
            self.log_callback(f"Error triggering call event to Pusher: {e}")
            return False
